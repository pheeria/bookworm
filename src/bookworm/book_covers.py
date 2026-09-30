"""Covers for books: generate from a book's details, edit, publish.

    POST   /books/{slug}/covers                   generate a draft
    GET    /books/{slug}/covers                   list, filter by status and type
    GET    /books/{slug}/covers/{id}
    PATCH  /books/{slug}/covers/{id}              color, theme
    POST   /books/{slug}/covers/{id}/regenerate   re-render, optionally for another type
    POST   /books/{slug}/covers/{id}/publish      put the short entry on the book
    POST   /books/{slug}/covers/{id}/unpublish
    DELETE /books/{slug}/covers/{id}
    GET    /cover-images/{image_id}.png       immutable; a new render gets a new URL

Nothing about a cover is picked by hand. The reader type decides how it feels
and what it may be set in (``covers.moods``); the publisher decides the
formalities (``bookworm.houses``); the book supplies the words. A caller only
chooses the type, and may rewrite the brief text.

The book is read, never written, except for the short entry of a published cover
in its ``generated_covers``, which is kept in step with the cover document.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, get_args

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError

from books import db as books_db
from books.models import Book, BookTheme, Color, GeneratedCover, ReaderType
from books.router import not_found
from covers.models import CoverRequest, CoverResult
from covers.moods import MOODS
from covers.palettes import luminance, rgb
from covers.pipeline import create_cover

from . import cover_store
from .houses import formalities

router = APIRouter(tags=["book covers"])

# The book's reader types are the covers' moods; the two packages name them apart.
assert set(get_args(ReaderType)) == set(MOODS), "ReaderType and covers.moods disagree"

Status = Literal["draft", "published"]


# --- Requests and responses ---------------------------------------------------


class BookCoverRequest(BaseModel):
    type: ReaderType = Field(description="Who the cover is for; decides its mood and faces.")
    text: str | None = Field(
        default=None, min_length=1,
        description="The brief the cover is derived from. Defaults to the book's blurb.",
    )


class RegenerateRequest(BookCoverRequest):
    type: ReaderType | None = Field(default=None, description="Defaults to the cover's own.")


class CoverPatch(BaseModel):
    color: Color | None = None
    theme: BookTheme | None = None


class BookCover(CoverResult):
    id: str
    book: str = Field(description="The book's slug.")
    type: ReaderType
    status: Status
    published_at: datetime | None = None
    url: str = Field(description="The front cover PNG.")
    color: str
    theme: BookTheme
    options: dict[str, Any] = Field(description="What the caller set (the brief text); regeneration reuses it.")
    request: dict[str, Any] = Field(description="The effective request: book, publisher, reader type and options.")
    created_at: datetime
    updated_at: datetime


# --- Book details -> cover request --------------------------------------------


def book_defaults(book: Book) -> dict[str, Any]:
    """The words from the book, the formalities from its publisher."""
    # The subtitle is worth briefing on only when it says more than the genre.
    adds = book.subtitle.strip().casefold() not in ("", book.category.strip().casefold())
    return {
        "text": f"{book.subtitle}. {book.blurb}" if adds else book.blurb,
        "title": book.title,
        "author": book.author,
        "genre_line": book.category,
        **formalities(book),
    }


# --- Page theme from the cover colour -----------------------------------------


def _hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{round(min(255, max(0, c))):02x}" for c in (r, g, b))


def _shade(color: str, k: float) -> str:
    return _hex(*(c * k for c in rgb(color)))


def _tint(color: str, t: float) -> str:
    return _hex(*(c + (255 - c) * t for c in rgb(color)))


def theme_for(color: str) -> BookTheme:
    """The page theme for a cover colour, by the rules the seed themes follow.

    Dark grounds get a deeper gradient and cream type; light grounds a paler
    gradient and type in a deep shade of the cover's own colour.
    """
    if luminance(color) < 0.45:
        start, end, cream = _shade(color, 0.85), _shade(color, 0.45), "#fbf8f3"
        return BookTheme(
            page=f"bg-gradient-to-br from-[{start}] to-[{end}]",
            text=f"text-[{cream}]",
            soft=f"text-[{cream}]/70",
            button=f"bg-[{cream}] text-[{end}]",
            ring="ring-white/15",
        )
    ink = _shade(color, 0.2)
    return BookTheme(
        page=f"bg-gradient-to-br from-[{_tint(color, 0.35)}] to-[{_tint(color, 0.65)}]",
        text=f"text-[{ink}]",
        soft=f"text-[{ink}]/70",
        button=f"bg-[{ink}] text-[{_tint(color, 0.8)}]",
        ring="ring-black/10",
    )


# --- Plumbing -----------------------------------------------------------------


class Stores:
    """Everything a cover endpoint touches, from the app's lifespan state."""

    def __init__(self, request: Request) -> None:
        state = request.app.state
        self.books, self.covers, self.images = state.books, state.covers, state.cover_images


Deps = Annotated[Stores, Depends()]


async def _book(stores: Stores, slug: str) -> tuple[ObjectId, Book]:
    found = await run_in_threadpool(books_db.find_book, stores.books, slug)
    if found is None:
        raise not_found(slug)
    return found


async def _book_id(stores: Stores, slug: str) -> ObjectId:
    """For endpoints that only need to know which book, not what it says."""
    found = await run_in_threadpool(books_db.book_id, stores.books, slug)
    if found is None:
        raise not_found(slug)
    return found


def _no_cover(cover_id: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"no cover {cover_id!r} for this book")


async def _cover(stores: Stores, book_id: ObjectId, cover_id: str) -> dict:
    doc = await run_in_threadpool(cover_store.get, stores.covers, book_id, cover_id)
    if doc is None:
        raise _no_cover(cover_id)
    return doc


async def _update(stores: Stores, book_id: ObjectId, cover_id: str, fields: dict) -> dict:
    doc = await run_in_threadpool(cover_store.update, stores.covers, book_id, cover_id, fields)
    if doc is None:
        raise _no_cover(cover_id)
    return doc


def _response(doc: dict, slug: str) -> BookCover:
    return BookCover.model_validate({**doc, "book": slug})


async def _render(
    stores: Stores, cover_id: str, book: Book, type: str, options: dict
) -> dict:
    """Generate from the book's current details, for ``type``; store the PNG."""
    try:
        request = CoverRequest.model_validate({**book_defaults(book), **options, "mood": type})
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    result = await create_cover(**request.model_dump())
    image_id = await run_in_threadpool(cover_store.put_image, stores.images, cover_id, result["png"])
    color = result["art_direction"]["ground"].lower()
    return {
        **{k: result[k] for k in CoverResult.model_fields},
        # A hash-derived seed can exceed BSON's 64-bit ints; BookCover reads it back.
        "seed": str(result["seed"]),
        "image_id": image_id,
        "url": f"/cover-images/{image_id}.png",
        "color": color,
        "theme": theme_for(color).model_dump(),
        "options": options,
        "request": request.model_dump(mode="json"),
    }


async def _sync_entry(stores: Stores, book_id: ObjectId, doc: dict) -> None:
    """Keep a published cover's short entry on the book in step with the cover."""
    if doc["status"] == "published":
        entry = GeneratedCover.model_validate(doc)
        await run_in_threadpool(books_db.put_cover_entry, stores.books, book_id, entry)


# --- Endpoints ----------------------------------------------------------------


@router.post("/books/{slug}/covers", response_model=BookCover, status_code=status.HTTP_201_CREATED)
async def create_book_cover(slug: str, body: BookCoverRequest, stores: Deps) -> BookCover:
    book_id, book = await _book(stores, slug)
    cover_id = uuid.uuid4().hex[:16]
    options = body.model_dump(exclude_unset=True, exclude={"type"})
    fields = await _render(stores, cover_id, book, body.type, options)
    doc = {"id": cover_id, "book_id": book_id, "type": body.type, "status": "draft",
           "published_at": None, **fields}
    doc = await run_in_threadpool(cover_store.insert, stores.covers, doc)
    return _response(doc, slug)


@router.get("/books/{slug}/covers", response_model=list[BookCover])
async def list_book_covers(
    slug: str, stores: Deps, status: Status | None = None, type: ReaderType | None = None
) -> list[BookCover]:
    book_id = await _book_id(stores, slug)
    docs = await run_in_threadpool(
        cover_store.list_for_book, stores.covers, book_id, status=status, type=type
    )
    return [_response(d, slug) for d in docs]


@router.get("/books/{slug}/covers/{cover_id}", response_model=BookCover)
async def get_book_cover(slug: str, cover_id: str, stores: Deps) -> BookCover:
    book_id = await _book_id(stores, slug)
    return _response(await _cover(stores, book_id, cover_id), slug)


@router.patch("/books/{slug}/covers/{cover_id}", response_model=BookCover)
async def edit_book_cover(slug: str, cover_id: str, body: CoverPatch, stores: Deps) -> BookCover:
    book_id = await _book_id(stores, slug)
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    doc = await _update(stores, book_id, cover_id, fields)
    await _sync_entry(stores, book_id, doc)
    return _response(doc, slug)


@router.post("/books/{slug}/covers/{cover_id}/regenerate", response_model=BookCover)
async def regenerate_book_cover(
    slug: str, cover_id: str, stores: Deps, body: RegenerateRequest | None = None
) -> BookCover:
    body = body or RegenerateRequest()
    book_id, book = await _book(stores, slug)
    old = await _cover(stores, book_id, cover_id)
    type = body.type or old["type"]
    # Only the brief text carries over: covers made before the manual options went
    # may still store them, and they must not come back through the stored copy.
    options = {k: v for k, v in old["options"].items() if k == "text"}
    options |= body.model_dump(exclude_unset=True, exclude={"type"})
    # The new image is stored before the old one goes, so a failed render
    # leaves the cover as it was.
    fields = {**await _render(stores, cover_id, book, type, options), "type": type}
    doc = await _update(stores, book_id, cover_id, fields)
    await asyncio.gather(
        run_in_threadpool(cover_store.delete_image, stores.images, old["image_id"]),
        _sync_entry(stores, book_id, doc),
    )
    return _response(doc, slug)


@router.post("/books/{slug}/covers/{cover_id}/publish", response_model=BookCover)
async def publish_book_cover(slug: str, cover_id: str, stores: Deps) -> BookCover:
    book_id = await _book_id(stores, slug)
    old = await _cover(stores, book_id, cover_id)
    fields = {"status": "published", "published_at": old["published_at"] or datetime.now(UTC)}
    doc = await _update(stores, book_id, cover_id, fields)
    await _sync_entry(stores, book_id, doc)
    return _response(doc, slug)


@router.post("/books/{slug}/covers/{cover_id}/unpublish", response_model=BookCover)
async def unpublish_book_cover(slug: str, cover_id: str, stores: Deps) -> BookCover:
    book_id = await _book_id(stores, slug)
    doc, _ = await asyncio.gather(
        _update(stores, book_id, cover_id, {"status": "draft", "published_at": None}),
        run_in_threadpool(books_db.remove_cover_entry, stores.books, book_id, cover_id),
    )
    return _response(doc, slug)


@router.delete("/books/{slug}/covers/{cover_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book_cover(slug: str, cover_id: str, stores: Deps) -> None:
    book_id = await _book_id(stores, slug)
    doc = await run_in_threadpool(cover_store.delete, stores.covers, book_id, cover_id)
    if doc is None:
        raise _no_cover(cover_id)
    await asyncio.gather(
        run_in_threadpool(books_db.remove_cover_entry, stores.books, book_id, cover_id),
        run_in_threadpool(cover_store.delete_image, stores.images, doc["image_id"]),
    )


@router.get("/cover-images/{image_id}.png", response_class=Response)
async def cover_image(image_id: str, stores: Deps) -> Response:
    """A cover's PNG. Each render stores a new image, so the URL never changes content."""
    try:
        oid = ObjectId(image_id)
    except InvalidId:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from None
    data = await run_in_threadpool(cover_store.read_image, stores.images, oid)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return Response(
        data, media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
