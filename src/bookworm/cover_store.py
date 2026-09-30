"""MongoDB storage for book covers: the ``covers`` collection and their PNGs.

A cover document is the full record of one generated cover -- what was asked for,
the brief, the geometry, its status. The book only ever carries the short entry of
a published cover (see ``books.db.put_cover_entry``). Images live in GridFS, in the
same database, because a deployed instance's disk does not outlive a deploy.
"""

from datetime import UTC, datetime

import gridfs
from bson import ObjectId
from pymongo import DESCENDING, IndexModel, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

_INDEXES = [
    IndexModel("id", unique=True),
    IndexModel([("book_id", 1), ("created_at", DESCENDING)]),
]


def init(db: Database) -> tuple[Collection, gridfs.GridFS]:
    covers = db["covers"]
    covers.create_indexes(_INDEXES)
    return covers, gridfs.GridFS(db, "cover_images")


def insert(covers: Collection, doc: dict) -> dict:
    now = datetime.now(UTC)
    doc = {**doc, "created_at": now, "updated_at": now}
    covers.insert_one(doc)
    return doc


def get(covers: Collection, book_id: ObjectId, cover_id: str) -> dict | None:
    return covers.find_one({"id": cover_id, "book_id": book_id})


def list_for_book(covers: Collection, book_id: ObjectId, **filters: str) -> list[dict]:
    query = {"book_id": book_id, **{k: v for k, v in filters.items() if v is not None}}
    return list(covers.find(query).sort("created_at", DESCENDING))


def update(covers: Collection, book_id: ObjectId, cover_id: str, fields: dict) -> dict | None:
    """Set ``fields`` and return the updated document, or None if there is none."""
    return covers.find_one_and_update(
        {"id": cover_id, "book_id": book_id},
        {"$set": {**fields, "updated_at": datetime.now(UTC)}},
        return_document=ReturnDocument.AFTER,
    )


def delete(covers: Collection, book_id: ObjectId, cover_id: str) -> dict | None:
    """Delete and return the cover, or None if there is none."""
    return covers.find_one_and_delete({"id": cover_id, "book_id": book_id})


def put_image(images: gridfs.GridFS, cover_id: str, png: bytes) -> ObjectId:
    return images.put(png, filename=f"{cover_id}.png", content_type="image/png")


def read_image(images: gridfs.GridFS, image_id: ObjectId) -> bytes | None:
    try:
        return images.get(image_id).read()
    except gridfs.NoFile:
        return None


def delete_image(images: gridfs.GridFS, image_id: ObjectId) -> None:
    images.delete(image_id)
