"""Request and response schemas for the HTTP API."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .artdirection import MOTIFS, STYLES, TEMPLATES, Artwork
from .formats import DEFAULT_FORMAT, FORMATS
from .imagegen import Quality, Treatment
from .palettes import PALETTE_KEYS
from .assets import ASSET_NAMES
from .typography import TYPE_FAMILIES


class CoverRequest(BaseModel):
    """A cover to generate.

    ``text`` is the brief: a description, a blurb, a synopsis or an excerpt.
    Everything else has a working default, and the override fields let you pin
    any part of the art direction the model would otherwise choose.
    """

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1, description="The prompt the cover is derived from.")
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)

    format: str = Field(
        default=DEFAULT_FORMAT,
        description=f"Trade format. One of: {', '.join(sorted(FORMATS))}.",
    )
    pages: int = Field(default=288, ge=16, le=2000, description="Drives the spine width.")
    dpi: int = Field(default=300, ge=72, le=900)

    imprint: str | None = Field(
        default=None, description="Publisher wordmark; defaults to the format's imprint."
    )
    isbn: str = ""
    price: str = Field(default="", description='e.g. "€ 24,00 [D]".')
    translator: str = Field(
        default="", description='e.g. "Aus dem Englischen von …".'
    )

    # --- art-direction overrides -------------------------------------------
    template: Literal[TEMPLATES] | None = None  # type: ignore[valid-type]
    type_family: Literal[TYPE_FAMILIES] | None = None  # type: ignore[valid-type]
    palette: Literal[PALETTE_KEYS] | None = None  # type: ignore[valid-type]
    artwork: Artwork | None = None
    motif: Literal[MOTIFS] | None = None  # type: ignore[valid-type]
    genre_line: str | None = Field(
        default=None, description='Gattungsbezeichnung, e.g. "Roman".'
    )
    blurb: str | None = Field(default=None, description="Override the back-cover copy.")
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
            "the image prompt locally from your text, giving up the palette, genre "
            "line and back-cover copy the brief would have decided. The brief is "
            "~9s of a ~144s cover, so this is a provider choice, not a speed one -- "
            "see image_quality."
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
    seed: int | None = Field(default=None, description="Pin the procedural motif.")
    marks: bool = Field(
        default=False, description="Draw trim and fold lines for proofing."
    )
    spine_direction: Literal["top_to_bottom", "bottom_to_top"] = "top_to_bottom"
    assets: list[Literal[ASSET_NAMES]] | None = Field(  # type: ignore[valid-type]
        default=None, description=f"Defaults to a useful subset of: {', '.join(ASSET_NAMES)}."
    )


CopySource = Literal["caller", "model", "fallback"]


class CopySuggestion(BaseModel):
    """A piece of book copy the cover printed, and where it came from."""

    value: str
    source: CopySource = Field(
        description=(
            "caller: you pinned it, the cover just used it. model: an art-direction "
            "model wrote it. fallback: the deterministic brief made it up because no "
            "model was reachable -- a fallback blurb is the first sentences of your "
            "own input text, so a book record should rarely adopt it."
        )
    )


class CopySuggestions(BaseModel):
    """Book copy the cover used. The book record decides whether to adopt it."""

    genre_line: CopySuggestion
    blurb: CopySuggestion


class CoverResponse(BaseModel):
    id: str
    assets: dict[str, str] = Field(description="Asset name to a URL you can GET.")
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


class FormatInfo(BaseModel):
    key: str
    label: str
    imprint: str
    binding: str
    trim_mm: list[float]
    flap_mm: float
    spine_mm_at_288pp: float


class CatalogueResponse(BaseModel):
    formats: list[FormatInfo]
    styles: list[str]
    templates: list[str]
    type_families: list[str]
    palettes: list[dict[str, Any]]
    motifs: list[str]
    assets: list[str]
