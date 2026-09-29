"""Request and response schemas for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .artdirection import MOTIFS, TEMPLATES
from .formats import DEFAULT_FORMAT, FORMATS
from .palettes import PALETTE_KEYS
from .render import ASSETS
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
    artwork: Literal["generated", "procedural", "none"] | None = None
    motif: Literal[MOTIFS] | None = None  # type: ignore[valid-type]
    genre_line: str | None = Field(
        default=None, description='Gattungsbezeichnung, e.g. "Roman".'
    )
    blurb: str | None = Field(default=None, description="Override the back-cover copy.")
    treatment: Literal["none", "duotone", "grayscale"] = Field(
        default="duotone",
        description="Post-process applied to generated artwork to hold the palette.",
    )

    # --- output ------------------------------------------------------------
    seed: int | None = Field(default=None, description="Pin the procedural motif.")
    marks: bool = Field(
        default=False, description="Draw trim and fold lines for proofing."
    )
    spine_direction: Literal["top_to_bottom", "bottom_to_top"] = "top_to_bottom"
    assets: list[Literal[ASSETS]] | None = Field(  # type: ignore[valid-type]
        default=None, description=f"Defaults to a useful subset of: {', '.join(ASSETS)}."
    )


class CoverResponse(BaseModel):
    id: str
    assets: dict[str, str] = Field(description="Asset name to a URL you can GET.")
    art_direction: dict[str, Any]
    art_direction_meta: dict[str, Any]
    artwork: dict[str, Any] | None
    geometry: dict[str, Any]
    content: dict[str, Any]
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
    templates: list[str]
    type_families: list[str]
    palettes: list[dict[str, Any]]
    motifs: list[str]
    assets: list[str]
