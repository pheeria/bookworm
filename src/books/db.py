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

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection

from .models import Book

SEED = Path(__file__).with_name("seed.json")

_collection: Collection | None = None


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


def init(client: MongoClient) -> None:
    """Point the API at ``client``, create indexes, and seed an empty collection."""
    global _collection
    _collection = client[os.environ.get("MONGODB_DB", "bookworm")]["books"]
    _collection.create_index("slug", unique=True)
    _collection.create_index("isbn", unique=True)
    for field in ("publisher", "category", "format"):
        _collection.create_index([(field, ASCENDING)])
    if _collection.estimated_document_count() == 0:
        now = datetime.now(UTC)
        _collection.insert_many(
            {**Book.model_validate(raw).model_dump(), "created_at": now, "updated_at": now}
            for raw in json.loads(SEED.read_text(encoding="utf-8"))
        )


def get_db() -> Collection:
    """FastAPI dependency: the books collection. The client pools connections itself."""
    if _collection is None:
        raise RuntimeError("books.db.init() has not been called")
    return _collection


def _book(doc: dict) -> Book:
    return Book.model_validate({key: doc[key] for key in Book.model_fields})


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

    total = books.count_documents(query)
    docs = books.find(query).sort("_id", ASCENDING).skip(offset).limit(limit)
    return [_book(d) for d in docs], total


def get_book(books: Collection, slug: str) -> Book | None:
    doc = books.find_one({"slug": slug})
    return _book(doc) if doc else None


def insert(books: Collection, book: Book) -> None:
    """Raises pymongo.errors.DuplicateKeyError on a duplicate slug or ISBN."""
    now = datetime.now(UTC)
    books.insert_one({**book.model_dump(), "created_at": now, "updated_at": now})


def replace(books: Collection, slug: str, book: Book) -> bool:
    """Replace the book at ``slug``. False if there is none; DuplicateKeyError on a clash."""
    result = books.update_one(
        {"slug": slug}, {"$set": {**book.model_dump(), "updated_at": datetime.now(UTC)}}
    )
    return result.matched_count > 0


def delete(books: Collection, slug: str) -> bool:
    return books.delete_one({"slug": slug}).deleted_count > 0
