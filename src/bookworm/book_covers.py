"""Covers for books: generate from a book's details, edit, publish.

    POST   /books/{slug}/covers                   generate a draft
    GET    /books/{slug}/covers                   list, filter by status and type
    GET    /books/{slug}/covers/{id}
    PATCH  /books/{slug}/covers/{id}              type, color, theme
    POST   /books/{slug}/covers/{id}/regenerate   re-render with merged options
    POST   /books/{slug}/covers/{id}/publish      put the short entry on the book
    POST   /books/{slug}/covers/{id}/unpublish
    DELETE /books/{slug}/covers/{id}
    GET    /cover-images/{id}.png

The book is read, never written, except for the short entry of a published cover
in its ``generated_covers``, which is kept in step with the cover document.
"""

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from books import db as books_db
from books.models import Book, BookTheme, Color, GeneratedCover, ReaderType
from covers.formats import DEFAULT_FORMAT
from covers.models import CoverRequest
from covers.palettes import luminance, rgb
from covers.pipeline import create_cover

from . import cover_store

router = APIRouter(tags=["book covers"])

Status = Literal["draft", "published"]


# --- Requests and responses ---------------------------------------------------


class CoverOptions(CoverRequest):
    """Any cover field, overriding what the book supplies. All optional."""

    text: str | None = Field(default=None, min_length=1, description="Defaults to the book's blurb.")
    title: str | None = Field(default=None, min_length=1, description="Defaults to the book's title.")
    author: str | None = Field(default=None, min_length=1, description="Defaults to the book's author.")
    format: str | None = Field(
        default=None, description="Defaults to the format matching the book's publisher and binding."
    )


class BookCoverRequest(CoverOptions):
    type: ReaderType


class CoverPatch(BaseModel):
    type: ReaderType | None = None
    color: Color | None = None
    theme: BookTheme | None = None


class BookCover(BaseModel):
    id: str
    book: str = Field(description="The book's slug.")
    type: ReaderType
    status: Status
    published_at: datetime | None = None
    url: str = Field(description="The front cover PNG.")
    color: str
    theme: BookTheme
    options: dict[str, Any] = Field(description="What the caller set; regeneration reuses it.")
    request: dict[str, Any] = Field(description="The effective request: book defaults plus options.")
    art_direction: dict[str, Any]
    art_direction_meta: dict[str, Any]
    artwork: dict[str, Any] | None
    geometry: dict[str, Any]
    content: dict[str, Any]
    suggestions: dict[str, Any]
    notes: list[str]
    style: str
    director: str
    seed: int
    created_at: datetime
    updated_at: datetime


# --- Book details -> cover request --------------------------------------------

#: (publisher, book format) -> covers format key, where covers knows the house.
_HOUSE_FORMATS = {
    ("Kiepenheuer & Witsch", "Hardcover"): "kiwi_hardcover",
    ("Kiepenheuer & Witsch", "Paperback"): "kiwi_paperback",
    ("Kiepenheuer & Witsch", "Taschenbuch"): "kiwi_taschenbuch",
    ("Kiepenheuer & Witsch", "Klappenbroschur"): "kiwi_klappenbroschur",
    ("Rowohlt", "Hardcover"): "rowohlt_hardcover",
    ("Rowohlt", "Paperback"): "rowohlt_paperback",
    ("Rowohlt", "Taschenbuch"): "rororo_taschenbuch",
}


def format_for(book: Book) -> str:
    if key := _HOUSE_FORMATS.get((book.publisher, book.format)):
        return key
    return "din_a5_hardcover" if book.format == "Hardcover" else DEFAULT_FORMAT


def book_defaults(book: Book) -> dict[str, Any]:
    # The subtitle is worth briefing on only when it says more than the genre.
    adds = book.subtitle.strip().casefold() not in ("", book.category.strip().casefold())
    return {
        "text": f"{book.subtitle}. {book.blurb}" if adds else book.blurb,
        "title": book.title,
        "author": book.author,
        "imprint": book.publisher,
        "genre_line": book.category,
        "format": format_for(book),
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no book {slug!r}")
    return found


async def _cover(stores: Stores, book_id: ObjectId, cover_id: str) -> dict:
    doc = await run_in_threadpool(cover_store.get, stores.covers, book_id, cover_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no cover {cover_id!r} for this book")
    return doc


def _entry(doc: dict) -> GeneratedCover:
    return GeneratedCover(**{k: doc[k] for k in ("id", "type", "url", "color", "theme")})


def _response(doc: dict, slug: str) -> BookCover:
    return BookCover.model_validate({**doc, "book": slug})


async def _render(stores: Stores, cover_id: str, book: Book, options: dict) -> dict:
    """Generate from the book's current details plus ``options``; store the PNG."""
    try:
        request = CoverRequest.model_validate({**book_defaults(book), **options})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = await create_cover(
                **request.model_dump(exclude={"format"}),
                format_key=request.format,
                outdir=Path(tmp),
            )
        except KeyError as exc:
            raise HTTPException(422, str(exc.args[0] if exc.args else exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        image_id = await run_in_threadpool(
            cover_store.put_image, stores.images, cover_id, Path(result["image"])
        )
    color = result["art_direction"]["ground"].lower()
    return {
        "image_id": image_id,
        "url": f"/cover-images/{cover_id}.png",
        "color": color,
        "theme": theme_for(color).model_dump(),
        "options": options,
        "request": request.model_dump(mode="json"),
        # A hash-derived seed can exceed BSON's 64-bit ints; BookCover reads it back.
        "seed": str(result["seed"]),
        **{k: result[k] for k in (
            "art_direction", "art_direction_meta", "artwork", "geometry", "content",
            "suggestions", "notes", "style", "director",
        )},
    }


async def _sync_entry(stores: Stores, book_id: ObjectId, doc: dict) -> None:
    """Keep a published cover's short entry on the book in step with the cover."""
    if doc["status"] == "published":
        await run_in_threadpool(books_db.put_cover_entry, stores.books, book_id, _entry(doc))


# --- Endpoints ----------------------------------------------------------------


@router.post("/books/{slug}/covers", response_model=BookCover, status_code=status.HTTP_201_CREATED)
async def create_book_cover(slug: str, body: BookCoverRequest, stores: Deps) -> BookCover:
    book_id, book = await _book(stores, slug)
    cover_id = uuid.uuid4().hex[:16]
    options = body.model_dump(exclude_unset=True, exclude={"type"})
    fields = await _render(stores, cover_id, book, options)
    doc = {"id": cover_id, "book_id": book_id, "type": body.type, "status": "draft",
           "published_at": None, **fields}
    doc = await run_in_threadpool(cover_store.insert, stores.covers, doc)
    return _response(doc, slug)


@router.get("/books/{slug}/covers", response_model=list[BookCover])
async def list_book_covers(
    slug: str, stores: Deps, status: Status | None = None, type: ReaderType | None = None
) -> list[BookCover]:
    book_id, _ = await _book(stores, slug)
    docs = await run_in_threadpool(
        cover_store.list_for_book, stores.covers, book_id, status=status, type=type
    )
    return [_response(d, slug) for d in docs]


@router.get("/books/{slug}/covers/{cover_id}", response_model=BookCover)
async def get_book_cover(slug: str, cover_id: str, stores: Deps) -> BookCover:
    book_id, _ = await _book(stores, slug)
    return _response(await _cover(stores, book_id, cover_id), slug)


@router.patch("/books/{slug}/covers/{cover_id}", response_model=BookCover)
async def edit_book_cover(slug: str, cover_id: str, body: CoverPatch, stores: Deps) -> BookCover:
    book_id, _ = await _book(stores, slug)
    await _cover(stores, book_id, cover_id)
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    doc = await run_in_threadpool(cover_store.update, stores.covers, book_id, cover_id, fields)
    await _sync_entry(stores, book_id, doc)
    return _response(doc, slug)


@router.post("/books/{slug}/covers/{cover_id}/regenerate", response_model=BookCover)
async def regenerate_book_cover(
    slug: str, cover_id: str, body: CoverOptions, stores: Deps
) -> BookCover:
    book_id, book = await _book(stores, slug)
    old = await _cover(stores, book_id, cover_id)
    options = {**old["options"], **body.model_dump(exclude_unset=True)}
    # The new image is stored before the old one goes, so a failed render
    # leaves the cover as it was.
    fields = await _render(stores, cover_id, book, options)
    doc = await run_in_threadpool(cover_store.update, stores.covers, book_id, cover_id, fields)
    await run_in_threadpool(cover_store.delete_image, stores.images, old["image_id"])
    await _sync_entry(stores, book_id, doc)
    return _response(doc, slug)


@router.post("/books/{slug}/covers/{cover_id}/publish", response_model=BookCover)
async def publish_book_cover(slug: str, cover_id: str, stores: Deps) -> BookCover:
    book_id, _ = await _book(stores, slug)
    old = await _cover(stores, book_id, cover_id)
    fields = {"status": "published", "published_at": old["published_at"] or datetime.now(UTC)}
    doc = await run_in_threadpool(cover_store.update, stores.covers, book_id, cover_id, fields)
    await _sync_entry(stores, book_id, doc)
    return _response(doc, slug)


@router.post("/books/{slug}/covers/{cover_id}/unpublish", response_model=BookCover)
async def unpublish_book_cover(slug: str, cover_id: str, stores: Deps) -> BookCover:
    book_id, _ = await _book(stores, slug)
    await _cover(stores, book_id, cover_id)
    fields = {"status": "draft", "published_at": None}
    doc = await run_in_threadpool(cover_store.update, stores.covers, book_id, cover_id, fields)
    await run_in_threadpool(books_db.remove_cover_entry, stores.books, book_id, cover_id)
    return _response(doc, slug)


@router.delete("/books/{slug}/covers/{cover_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book_cover(slug: str, cover_id: str, stores: Deps) -> None:
    book_id, _ = await _book(stores, slug)
    doc = await _cover(stores, book_id, cover_id)
    await run_in_threadpool(books_db.remove_cover_entry, stores.books, book_id, cover_id)
    await run_in_threadpool(cover_store.delete, stores.covers, book_id, cover_id)
    await run_in_threadpool(cover_store.delete_image, stores.images, doc["image_id"])


@router.get("/cover-images/{cover_id}.png", response_class=Response)
async def cover_image(cover_id: str, stores: Deps) -> Response:
    doc = await run_in_threadpool(stores.covers.find_one, {"id": cover_id}, {"image_id": 1})
    data = doc and await run_in_threadpool(cover_store.read_image, stores.images, doc["image_id"])
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return Response(data, media_type="image/png")
