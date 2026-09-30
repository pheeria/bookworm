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

from bson import ObjectId
from fastapi import Request
from pymongo import IndexModel, MongoClient
from pymongo.collection import Collection

from .models import Book, GeneratedCover

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
    found = find_book(books, slug)
    return found[1] if found else None


def insert(books: Collection, book: Book) -> Book:
    """Store a new book, with no covers. DuplicateKeyError on a duplicate slug or ISBN.

    ``generated_covers`` is not writable here: entries appear when a cover is
    published (see ``put_cover_entry``).
    """
    now = datetime.now(UTC)
    book = book.model_copy(update={"generated_covers": []})
    books.insert_one(_document(book, created_at=now, updated_at=now))
    return book


def replace(books: Collection, slug: str, book: Book) -> Book | None:
    """Replace the book at ``slug``, keeping its published cover entries.

    Returns the stored book, or None if there is none; DuplicateKeyError on a clash.
    """
    fields = _document(book, updated_at=datetime.now(UTC))
    del fields["generated_covers"]
    # Not find_one_and_update: mongomock returns None from it when the update
    # changes the field the filter matched on (a slug rename), unlike MongoDB.
    if not books.update_one({"slug": slug}, {"$set": fields}).matched_count:
        return None
    return get_book(books, book.slug)


def delete(books: Collection, slug: str) -> bool:
    return books.delete_one({"slug": slug}).deleted_count > 0


# --- Published cover entries -------------------------------------------------
# The only writes other packages make to a book: the short cover entries in
# generated_covers. Keyed by the book's _id so they survive a slug rename.


def find_book(books: Collection, slug: str) -> tuple[ObjectId, Book] | None:
    """The book at ``slug`` with its database id, for linking records to it."""
    doc = books.find_one({"slug": slug}, {"created_at": 0, "updated_at": 0})
    return (doc.pop("_id"), Book.model_validate(doc)) if doc else None


def find_by_title(books: Collection, title: str) -> list[tuple[ObjectId, Book]]:
    """Books whose title is ``title``, ignoring case and surrounding whitespace."""
    pattern = {"$regex": f"^{re.escape(title.strip())}$", "$options": "i"}
    docs = books.find({"title": pattern}, {"created_at": 0, "updated_at": 0})
    return [(doc.pop("_id"), Book.model_validate(doc)) for doc in docs]


def book_id(books: Collection, slug: str) -> ObjectId | None:
    """Just the database id of the book at ``slug``."""
    doc = books.find_one({"slug": slug}, {"_id": 1})
    return doc["_id"] if doc else None


def put_cover_entry(books: Collection, book_id: ObjectId, entry: GeneratedCover) -> None:
    """Replace the entry with ``entry.id``, or append it if the book has none."""
    now = datetime.now(UTC)
    value = entry.model_dump()
    replaced = books.update_one(
        {"_id": book_id, "generated_covers.id": entry.id},
        {"$set": {"generated_covers.$": value, "updated_at": now}},
    )
    if not replaced.matched_count:
        books.update_one(
            {"_id": book_id}, {"$push": {"generated_covers": value}, "$set": {"updated_at": now}}
        )


def remove_cover_entry(books: Collection, book_id: ObjectId, cover_id: str) -> None:
    books.update_one(
        {"_id": book_id},
        {"$pull": {"generated_covers": {"id": cover_id}}, "$set": {"updated_at": datetime.now(UTC)}},
    )
