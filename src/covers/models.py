"""Request and response schemas for the HTTP API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .artdirection import STYLES, Artwork, Motif, Template, TypeFamily
from .formats import DEFAULT_FORMAT, FORMATS
from .imagegen import Quality, Treatment
from .palettes import PALETTE_KEYS

FormatKey = Literal[tuple(FORMATS)]  # type: ignore[valid-type]


class CoverRequest(BaseModel):
    """A front cover to generate.

    ``text`` is the brief: a description, a blurb, a synopsis or an excerpt.
    Everything else has a working default, and the override fields let you pin
    any part of the art direction the model would otherwise choose.
    """

    text: str = Field(min_length=1, description="The prompt the cover is derived from.")
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)

    format: FormatKey = Field(default=DEFAULT_FORMAT, description="Trade format.")
    dpi: int = Field(default=300, ge=72, le=900)

    imprint: str | None = Field(
        default=None, description="Publisher wordmark; defaults to the format's imprint."
    )

    # --- art-direction overrides -------------------------------------------
    template: Template | None = None
    type_family: TypeFamily | None = None
    palette: Literal[PALETTE_KEYS] | None = None  # type: ignore[valid-type]
    artwork: Artwork | None = None
    motif: Motif | None = None
    genre_line: str | None = Field(
        default=None, description='Gattungsbezeichnung, e.g. "Roman".'
    )
    style: Literal[STYLES] = Field(  # type: ignore[valid-type]
        default="illustrated",
        description=(
            "The register the cover is briefed in. illustrated: a drawn, figurative, "
            "warmly coloured picture carrying the cover. painterly: a painting, "
            "atmospheric and tonal. typographic: type-led and austere, imagery "
            "abstract or absent."
        ),
    )
    director: Literal["claude", "openai", "none"] = Field(
        default="claude",
        description=(
            "Who writes the brief. 'claude' and 'openai' both produce the same "
            "structured brief; 'none' skips the text model entirely and composes "
            "the image prompt locally from your text, giving up the palette and "
            "genre line the brief would have decided. The brief is a small part of "
            "the wall clock, so this is a provider choice, not a speed one -- see "
            "image_quality."
        ),
    )
    image_quality: Quality | None = Field(
        default=None,
        description=(
            "Image-model quality. This is the real latency and cost lever: 'high' "
            "(the default) is most of the request's wall clock. Defaults to "
            "COVERS_IMAGE_QUALITY, else 'high'."
        ),
    )
    treatment: Treatment = Field(
        default="none",
        description=(
            "Post-process applied to generated artwork. 'duotone' maps it onto two "
            "palette colours, which unifies but discards all hue; 'none' keeps the "
            "artwork's own colour."
        ),
    )

    # --- output ------------------------------------------------------------
    # Bounded to a signed 64-bit int so a pinned seed can be stored in BSON.
    seed: int | None = Field(default=None, ge=0, lt=2**63, description="Pin the procedural motif.")
    marks: bool = Field(default=False, description="Draw the trim box for proofing.")


CopySource = Literal["caller", "model", "fallback"]


class CopySuggestion(BaseModel):
    """A piece of book copy the cover printed, and where it came from."""

    value: str
    source: CopySource = Field(
        description=(
            "caller: you pinned it, the cover just used it. model: an art-direction "
            "model wrote it. fallback: the deterministic brief guessed it from keywords "
            "because no model was reachable, so a book record should rarely adopt it."
        )
    )


class CopySuggestions(BaseModel):
    """Book copy the cover used. The book record decides whether to adopt it."""

    genre_line: CopySuggestion


class CoverResult(BaseModel):
    """What ``create_cover`` reports about a cover, besides the PNG itself."""

    art_direction: dict[str, Any]
    art_direction_meta: dict[str, Any]
    artwork: dict[str, Any] | None
    geometry: dict[str, Any]
    content: dict[str, Any]
    suggestions: CopySuggestions = Field(
        description="Book copy this cover printed, tagged with who wrote it."
    )
    style: str
    director: str
    seed: int
    notes: list[str] = Field(
        description="Anything the renderer had to work around, in plain language."
    )


class CoverResponse(CoverResult):
    id: str
    image: str = Field(description="URL of the front cover PNG.")


class FormatInfo(BaseModel):
    key: str
    label: str
    imprint: str
    binding: str
    trim_mm: list[float]


class CatalogueResponse(BaseModel):
    formats: list[FormatInfo]
    styles: list[str]
    templates: list[str]
    type_families: list[str]
    palettes: list[dict[str, Any]]
    motifs: list[str]
