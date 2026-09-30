"""Covers for books: generate from a book's details, or upload one; edit, publish.

    POST   /books/{slug}/covers                   generate a draft
    GET    /books/{slug}/covers                   list, filter by status and type
    GET    /books/{slug}/covers/{id}
    PATCH  /books/{slug}/covers/{id}              color, theme
    POST   /books/{slug}/covers/{id}/regenerate   re-render, optionally for another type
    POST   /books/{slug}/covers/{id}/publish      put the short entry on the book
    POST   /books/{slug}/covers/{id}/unpublish
    DELETE /books/{slug}/covers/{id}
    POST   /covers/upload                     add a finished cover to its book, published
    GET    /cover-images/{image_id}.png       immutable; a new render gets a new URL

Nothing about a generated cover is picked by hand. The reader type decides how
it feels and what it may be set in (``covers.moods``); the publisher decides the
formalities (``bookworm.houses``); the book supplies the words. A caller only
chooses the type, and may rewrite the brief text. An uploaded cover is the
caller's own image; only its colour and theme are derived.

The book is read, never written, except for the short entry of a published cover
in its ``generated_covers``, which is kept in step with the cover document.
"""

import asyncio
import io
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, get_args

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from PIL import Image
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

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


class _BookCoverBase(BaseModel):
    """What every cover of a book has, however it was made."""

    id: str
    book: str = Field(description="The book's slug.")
    type: ReaderType
    status: Status
    published_at: datetime | None = None
    url: str = Field(description="The front cover PNG.")
    color: str
    theme: BookTheme
    options: dict[str, Any] = Field(description="What the caller set (the brief text); regeneration reuses it.")
    created_at: datetime
    updated_at: datetime


class GeneratedBookCover(_BookCoverBase, CoverResult):
    """Rendered here: the brief, geometry and request that made it come with it."""

    source: Literal["generated"] = "generated"
    color: str = Field(description="The brief's ground colour.")
    request: dict[str, Any] = Field(
        description="The effective request: book, publisher, reader type and options."
    )


class UploadedBookCover(_BookCoverBase):
    """Made elsewhere and uploaded: an image, and what was derived from it."""

    source: Literal["uploaded"]
    color: str = Field(description="Sampled from the image.")


BookCover = Annotated[GeneratedBookCover | UploadedBookCover, Field(discriminator="source")]
_BookCoverAdapter = TypeAdapter(BookCover)


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


def dominant_color(img: Image.Image) -> str:
    """The cover's most common colour once reduced to five: its ground, usually."""
    # Shrink before converting, so a large upload is never copied at full size.
    small = img.resize((96, 144), Image.Resampling.BOX).convert("RGB")
    quantized = small.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    _, index = max(quantized.getcolors())
    return _hex(*quantized.getpalette()[index * 3 : index * 3 + 3])


#: Larger than any cover a browser should be sending.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _decode_upload(data: bytes) -> tuple[bytes, str]:
    """The upload as PNG, and its colour. 422 for anything that is not an image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            color = dominant_color(img)
            # Stored as PNG like every other cover, so /cover-images serves one type.
            # A PNG that is already RGB or RGBA is kept exactly as uploaded.
            if img.format == "PNG" and img.mode in ("RGB", "RGBA"):
                return data, color
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue(), color
    except (OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "the file is not an image Pillow can read"
        ) from exc


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
    # Covers stored before uploads existed carry no source: they were all rendered.
    return _BookCoverAdapter.validate_python({"source": "generated", **doc, "book": slug})


def _new_cover_id() -> str:
    return uuid.uuid4().hex[:16]


def _image_fields(image_id: ObjectId, color: str) -> dict:
    """Where a cover's stored PNG is served, and the colour and theme that go with it."""
    return {
        "image_id": image_id,
        "url": f"/cover-images/{image_id}.png",
        "color": color,
        "theme": theme_for(color).model_dump(),
    }


def _published(since: datetime | None = None) -> dict:
    """The fields of a published cover; republishing keeps the first date."""
    return {"status": "published", "published_at": since or datetime.now(UTC)}


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
        "source": "generated",
        **_image_fields(image_id, color),
        "options": options,
        "request": request.model_dump(mode="json"),
    }


async def _sync_entry(stores: Stores, book_id: ObjectId, doc: dict, *, new: bool = False) -> None:
    """Keep a published cover's short entry on the book in step with the cover."""
    if doc["status"] == "published":
        entry = GeneratedCover.model_validate(doc)
        await run_in_threadpool(books_db.put_cover_entry, stores.books, book_id, entry, new=new)


# --- Endpoints ----------------------------------------------------------------


@router.post("/books/{slug}/covers", response_model=BookCover, status_code=status.HTTP_201_CREATED)
async def create_book_cover(slug: str, body: BookCoverRequest, stores: Deps) -> BookCover:
    book_id, book = await _book(stores, slug)
    cover_id = _new_cover_id()
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
    doc = await _update(stores, book_id, cover_id, _published(old["published_at"]))
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


@router.post("/covers/upload", response_model=BookCover, status_code=status.HTTP_201_CREATED)
async def upload_book_cover(
    stores: Deps,
    file: Annotated[UploadFile, File(description="The finished front cover, any common image format.")],
    title: Annotated[str, Form(min_length=1, description="The book's title, matched ignoring case.")],
    type: Annotated[ReaderType, Form(description="Who the cover is for.")],
) -> BookCover:
    """Add a cover made elsewhere to the book with this title, and publish it.

    An upload is already a finished cover, so it goes straight into the book's
    ``generated_covers``; unpublish it like any other to take it off. The colour is
    sampled from the image and the page theme derived from it, as for a generated
    cover; the image is stored as PNG in GridFS.
    """
    title = title.strip()
    matches = await run_in_threadpool(books_db.find_by_title, stores.books, title)
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no book titled {title!r}")
    if len(matches) > 1:
        slugs = ", ".join(book.slug for _, book in matches)
        raise HTTPException(status.HTTP_409_CONFLICT, f"several books are titled {title!r}: {slugs}")
    book_id, book = matches[0]

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"covers are limited to {MAX_UPLOAD_BYTES // 2**20} MB"
        )
    png, color = await run_in_threadpool(_decode_upload, data)

    cover_id = _new_cover_id()
    image_id = await run_in_threadpool(cover_store.put_image, stores.images, cover_id, png)
    doc = {
        "id": cover_id, "book_id": book_id, "type": type, "source": "uploaded",
        "options": {}, **_published(), **_image_fields(image_id, color),
    }
    doc, _ = await asyncio.gather(
        run_in_threadpool(cover_store.insert, stores.covers, doc),
        _sync_entry(stores, book_id, doc, new=True),
    )
    return _response(doc, book.slug)


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
