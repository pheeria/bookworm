"""Covers for books: generate from a book's details, or upload one; edit, publish.

    POST   /books/{slug}/covers                   generate a draft
    GET    /books/{slug}/covers                   list, filter by status and type
    GET    /books/{slug}/covers/{id}
    PATCH  /books/{slug}/covers/{id}              color, theme
    POST   /books/{slug}/covers/{id}/regenerate   re-render, optionally for another type
    POST   /books/{slug}/covers/{id}/publish      put the short entry on the book
    POST   /books/{slug}/covers/{id}/unpublish
    DELETE /books/{slug}/covers/{id}
    GET    /books/{slug}/core                  the Buchkern the covers are built on
    POST   /books/{slug}/core                  research the book again and rebuild it
    POST   /covers/upload                     add a finished cover to its book, published
    GET    /cover-images/{image_id}.png       immutable; a new render gets a new URL

Nothing about a generated cover is picked by hand. The reader type decides how
it feels and what it may be set in (``covers.moods``); the publisher decides the
formalities (``bookworm.houses``); the book supplies the words. A caller only
chooses the type, and may rewrite the brief text. An uploaded cover is the
caller's own image; only its colour and theme are derived.

With Claude as director, a generated cover rests on the book's Buchkern
(``covers.core``: researched on Wikipedia and Goodreads, cached per book) and on
three concepts for its reader type (``covers.concepts``); one is rendered, the
other two are kept to render on request. Without Claude it falls back to the
plain brief.

The book is read, never written, except for the short entry of a published cover
in its ``generated_covers``, which is kept in step with the cover document.
"""

import asyncio
import hashlib
import io
import json
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
from PIL import ExifTags, Image, ImageCms, ImageOps
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from books import db as books_db
from books.models import Book, BookTheme, Color, GeneratedCover, ReaderType
from books.router import not_found
from covers import concepts as covers_concepts
from covers import core as covers_core
from covers.imagegen import DEFAULT_IMAGE_MODEL, ImageModel
from covers.models import CoverRequest, CoverResult
from covers.moods import MOODS
from covers.palettes import luminance, rgb
from covers.pipeline import create_cover
from covers.profiles import PROFILES

from . import cover_store
from .houses import formalities

router = APIRouter(tags=["book covers"])

# The book's reader types are the covers' moods; the two packages name them apart.
assert set(get_args(ReaderType)) == set(MOODS), "ReaderType and covers.moods disagree"

Status = Literal["draft", "published"]


# --- Requests and responses ---------------------------------------------------


class BookCoverRequest(BaseModel):
    type: ReaderType = Field(description="Who the cover is for; decides its mood and faces.")
    image_model: ImageModel = Field(
        default=DEFAULT_IMAGE_MODEL,
        description=CoverRequest.model_fields["image_model"].description,
    )
    text: str | None = Field(
        default=None, min_length=1,
        description="The brief the cover is derived from. Defaults to the book's blurb.",
    )


class RegenerateRequest(BookCoverRequest):
    type: ReaderType | None = Field(default=None, description="Defaults to the cover's own.")
    image_model: ImageModel | None = Field(default=None, description="Defaults to the cover's own.")
    concept: int | None = Field(
        default=None, ge=0, le=2,
        description="Render one of the cover's stored concepts (0 is the main one) instead of new ones.",
    )


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
    concepts: dict[str, Any] | None = Field(
        default=None, description="The three concepts for this reader type, when Claude wrote them."
    )
    concept: int | None = Field(default=None, description="Which of the concepts this render is.")


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
        # A book without a blurb is still briefed, on its title.
        "text": (f"{book.subtitle}. {book.blurb}" if adds else book.blurb).strip() or book.title,
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
    """The cover's most common colour once reduced to five: its ground, usually.
    Transparency counts as white, the page it would sit on."""
    small = img.convert("RGBA").resize((96, 144), Image.Resampling.BOX)
    flat = Image.alpha_composite(Image.new("RGBA", small.size, "white"), small).convert("RGB")
    quantized = flat.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    _, index = max(quantized.getcolors())
    return _hex(*quantized.getpalette()[index * 3 : index * 3 + 3])


#: Larger than any cover a browser should be sending.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _needs_conversion(img: Image.Image) -> bool:
    rotated = img.getexif().get(ExifTags.Base.Orientation, 1) != 1
    return rotated or img.mode not in ("RGB", "RGBA")


def _as_rgb(img: Image.Image) -> Image.Image:
    """``img`` upright, in 8-bit sRGB, with an alpha channel if it had transparency."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGB", "RGBA"):
        return img
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        # Wide greys: scale to 8 bits rather than let convert() clip them to white.
        top = 2**16 - 1 if img.mode.startswith("I;16") else 2**32 - 1
        img = img.point(lambda v: v * 255 / top, "L")
    if icc := img.info.get("icc_profile"):
        # A print (CMYK) file: through its own profile, not naively; the profile
        # describes the old colours, so it does not come along.
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        img = ImageCms.profileToProfile(img, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        img.info.pop("icc_profile", None)
    transparent = "A" in img.getbands() or "transparency" in img.info
    return img.convert("RGBA" if transparent else "RGB")


def _decode_upload(data: bytes) -> tuple[bytes, str]:
    """The upload as PNG, and its colour. 422 for anything that is not an image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            # Stored as PNG like every other cover, so /cover-images serves one type.
            # A PNG that needs no change is kept exactly as uploaded.
            if img.format == "PNG" and not _needs_conversion(img):
                return data, dominant_color(img)
            converted = _as_rgb(img)
            out = io.BytesIO()
            converted.save(out, format="PNG")
            return out.getvalue(), dominant_color(converted)
    # Pillow reports a damaged file in many ways; each is the caller's bad image.
    except (OSError, ValueError, SyntaxError, IndexError, EOFError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "the file is not an image Pillow can read"
        ) from exc


# --- Plumbing -----------------------------------------------------------------


class Stores:
    """Everything a cover endpoint touches, from the app's lifespan state."""

    def __init__(self, request: Request) -> None:
        state = request.app.state
        self.books, self.covers, self.images = state.books, state.covers, state.cover_images
        self.cores = state.cores


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


# --- The Buchkern and its concepts ---------------------------------------------


def _title_details(book: Book) -> dict[str, str]:
    """The title data Prompt A reads, under the names it knows them by."""
    return {
        "Titel": book.title, "Untertitel": book.subtitle, "Autor*in": book.author,
        "Verlag": book.publisher, "Kategorie": book.category, "Ausgabe": book.format,
        "Seiten": str(book.pages), "Klappentext": book.blurb,
    }


def _fingerprint(book: Book) -> str:
    """Changes when the book does, so a stale Buchkern is rebuilt."""
    raw = json.dumps(_title_details(book), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _book_core(
    stores: Stores, book_id: ObjectId, book: Book, *, refresh: bool = False
) -> dict | None:
    """The cached Buchkern, or a fresh one: research, then Prompt A. None without Claude."""
    fingerprint = _fingerprint(book)
    if not refresh:
        cached = await run_in_threadpool(cover_store.get_core, stores.cores, book_id)
        if cached and cached["fingerprint"] == fingerprint:
            return cached
    details = _title_details(book)
    found = await covers_core.research(details)
    # A failed search is not the same as finding nothing: a rebuild asked for
    # fails, and a cover goes ahead on this core without it being kept.
    if found is None and refresh:
        return None
    core = await covers_core.write_core(
        details, found, publisher=book.publisher, imprint=formalities(book)["imprint"]
    )
    if core is None:
        return None
    doc = {
        "fingerprint": fingerprint,
        "core": core.model_dump(),
        "research": (found.notes or None) if found else None,
        "sources": found.sources if found else [],
    }
    if found is None:
        return doc
    return await run_in_threadpool(cover_store.put_core, stores.cores, book_id, doc)


async def _concept_brief(
    stores: Stores, book_id: ObjectId, book: Book, type: str,
    concepts: dict | None, index: int,
) -> tuple[Any, dict, list[str]] | None:
    """The brief for concept ``index``, writing concepts unless ``concepts`` holds them.

    Stored concepts need no Claude: they are rendered from the Buchkern already
    kept, even if the book has changed since.
    """
    if concepts is not None:
        record = await run_in_threadpool(cover_store.get_core, stores.cores, book_id)
    elif covers_core.enabled():
        record = await _book_core(stores, book_id, book)
    else:
        return None
    if record is None:
        return None
    core, mood = covers_core.BookCore.model_validate(record["core"]), MOODS[type]
    if concepts is None:
        written = await covers_concepts.write_concepts(core, mood, publisher=book.publisher)
        if written is None:
            return None
        concepts = written.model_dump()
    parsed = covers_concepts.load_concepts(mood, concepts)
    if index >= len(parsed.concepts):
        raise HTTPException(422, f"this cover has only {len(parsed.concepts)} concepts")
    fit = getattr(core.fit, type)
    notes = [] if fit != "ungeeignet" else [
        f"the Buchkern rates this title „ungeeignet“ for {PROFILES[type].label}"
    ]
    brief = covers_concepts.to_direction(core, mood, parsed, index)
    meta = {"source": "concept", "concept": index, "fit": fit, "sources": record["sources"]}
    return brief, {"concepts": concepts, "concept": index, "meta": meta}, notes


async def _render(
    stores: Stores, cover_id: str, book: Book, type: str, options: dict,
    *, book_id: ObjectId, concepts: dict | None = None, concept: int = 0,
) -> dict:
    """Generate from the book's current details, for ``type``; store the PNG."""
    try:
        request = CoverRequest.model_validate({**book_defaults(book), **options, "mood": type})
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    planned = await _concept_brief(stores, book_id, book, type, concepts, concept)
    extra: dict = {}
    if planned is None:
        result = await create_cover(**request.model_dump())
    else:
        brief, extra, notes = planned
        # The Buchkern's Typo-Daten decide the genre line; the book's category was its input.
        result = await create_cover(
            **request.model_dump(exclude={"genre_line"}), brief=brief, brief_meta=extra.pop("meta")
        )
        result["notes"] = notes + result["notes"]
    image_id = await run_in_threadpool(cover_store.put_image, stores.images, cover_id, result["png"])
    color = result["art_direction"]["ground"].lower()
    return {
        **{k: result[k] for k in CoverResult.model_fields},
        "source": "generated",
        **_image_fields(image_id, color),
        "options": options,
        "request": request.model_dump(mode="json"),
        # Kept even when the plain brief made this render, so they can still be asked for.
        "concepts": extra.get("concepts", concepts),
        "concept": extra.get("concept"),
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
    options = body.model_dump(exclude_unset=True, exclude_none=True, exclude={"type"})
    fields = await _render(stores, cover_id, book, body.type, options, book_id=book_id)
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
    # A stored concept belongs to the type it was written for.
    if body.concept is not None and (type != old["type"] or not old.get("concepts")):
        raise HTTPException(422, "this cover has no stored concepts for that type; regenerate without `concept`")
    # Only the brief text and the image model carry over: covers made before the
    # manual options went may still store others, which must not come back.
    options = {k: v for k, v in old["options"].items() if k in ("text", "image_model")}
    options |= body.model_dump(exclude_unset=True, exclude_none=True, exclude={"type", "concept"})
    # The new image is stored before the old one goes, so a failed render
    # leaves the cover as it was.
    reuse = old.get("concepts") if body.concept is not None else None
    fields = await _render(
        stores, cover_id, book, type, options,
        book_id=book_id, concepts=reuse, concept=body.concept or 0,
    )
    fields["type"] = type
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
    doc = await _update(stores, book_id, cover_id, {"status": "draft", "published_at": None})
    await run_in_threadpool(books_db.remove_cover_entry, stores.books, book_id, cover_id)
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


class BookCoreRecord(BaseModel):
    book: str
    core: covers_core.BookCore
    research: str | None = Field(description="The notes from Wikipedia and Goodreads.")
    sources: list[str] = Field(description="The pages the research drew on.")
    updated_at: datetime


@router.get("/books/{slug}/core", response_model=BookCoreRecord)
async def get_book_core(slug: str, stores: Deps) -> BookCoreRecord:
    book_id = await _book_id(stores, slug)
    record = await run_in_threadpool(cover_store.get_core, stores.cores, book_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no Buchkern yet; generate a cover or POST here")
    return BookCoreRecord.model_validate({**record, "book": slug})


@router.post("/books/{slug}/core", response_model=BookCoreRecord)
async def rebuild_book_core(slug: str, stores: Deps) -> BookCoreRecord:
    """Research the book again and rewrite its Buchkern. Costs a few web searches."""
    book_id, book = await _book(stores, slug)
    if not covers_core.enabled():
        raise HTTPException(status.HTTP_409_CONFLICT, "the Buchkern needs Claude as director")
    record = await _book_core(stores, book_id, book, refresh=True)
    if record is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Claude is unavailable; see the logs")
    return BookCoreRecord.model_validate({**record, "book": slug})


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
    # The cover first: a book entry must never point at a cover that is not there.
    doc = await run_in_threadpool(cover_store.insert, stores.covers, doc)
    await _sync_entry(stores, book_id, doc, new=True)
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
