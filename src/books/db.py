"""MongoDB storage for books.

One document per book in the ``books`` collection, covers embedded: nothing
queries into them, and they are only ever read and written with their book.
``slug`` and ``isbn`` carry unique indexes; ``_id`` stays a driver-made ObjectId
so a PUT can rename a slug in place.

Settings, read at ``connect()`` rather than import:

    MONGODB_URI        mongodb+srv://cluster0.example.mongodb.net  (required)
    MONGODB_USERNAME   override credentials in the URI, if either is set
    MONGODB_PASSWORD
    MONGODB_DB         database name, default "bookworm"
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from pymongo import IndexModel, MongoClient
from pymongo.collection import Collection

from .models import Book

SEED = Path(__file__).with_name("seed.json")

_INDEXES = [
    IndexModel("slug", unique=True),
    IndexModel("isbn", unique=True),
    IndexModel("publisher"),
    IndexModel("category"),
    IndexModel("format"),
]
# Books as the API returns them: without the driver's _id or our timestamps.
_PROJECTION = {"_id": 0, "created_at": 0, "updated_at": 0}


def connect() -> MongoClient:
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("MONGODB_URI is not set; the books API needs a MongoDB connection string")
    credentials = {
        key: value
        for key, var in (("username", "MONGODB_USERNAME"), ("password", "MONGODB_PASSWORD"))
        if (value := os.environ.get(var))
    }
    return MongoClient(uri, appname="bookworm", tz_aware=True, **credentials)


def init(client: MongoClient) -> Collection:
    """Return the books collection, indexed, with an empty one seeded."""
    books = client[os.environ.get("MONGODB_DB", "bookworm")]["books"]
    books.create_indexes(_INDEXES)  # one round trip; a no-op once they exist
    if books.estimated_document_count() == 0:
        now = datetime.now(UTC)
        books.insert_many(
            _document(Book.model_validate(raw), created_at=now, updated_at=now)
            for raw in json.loads(SEED.read_text(encoding="utf-8"))
        )
    return books


def get_db(request: Request) -> Collection:
    """FastAPI dependency: the collection the app's lifespan stored on its state."""
    return request.app.state.books


def _document(book: Book, **timestamps: datetime) -> dict:
    return {**book.model_dump(), **timestamps}


def list_books(
    books: Collection,
    *,
    publisher: str | None = None,
    category: str | None = None,
    format: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Book], int]:
    query: dict = {
        field: value
        for field, value in (("publisher", publisher), ("category", category), ("format", format))
        if value is not None
    }
    if q:
        pattern = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{field: pattern} for field in ("title", "author", "subtitle")]

    cursor = books.find(query, _PROJECTION).sort("_id").skip(offset).limit(limit)
    items = [Book.model_validate(d) for d in cursor.batch_size(limit)]
    # A short, non-empty page (or an empty first page) already tells us the total.
    if 0 < len(items) < limit or (not items and offset == 0):
        return items, offset + len(items)
    return items, books.count_documents(query)


def get_book(books: Collection, slug: str) -> Book | None:
    doc = books.find_one({"slug": slug}, _PROJECTION)
    return Book.model_validate(doc) if doc else None


def insert(books: Collection, book: Book) -> None:
    """Raises pymongo.errors.DuplicateKeyError on a duplicate slug or ISBN."""
    now = datetime.now(UTC)
    books.insert_one(_document(book, created_at=now, updated_at=now))


def replace(books: Collection, slug: str, book: Book) -> bool:
    """Replace the book at ``slug``. False if there is none; DuplicateKeyError on a clash."""
    result = books.update_one(
        {"slug": slug}, {"$set": _document(book, updated_at=datetime.now(UTC))}
    )
    return result.matched_count > 0


def delete(books: Collection, slug: str) -> bool:
    return books.delete_one({"slug": slug}).deleted_count > 0
