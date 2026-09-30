"""Lettering: where and how the type is set on a ``picture`` cover.

The concept plans it -- position, size, face, colour -- and the image prompt
keeps that area of the picture calm (``zone_text``). Once the picture is painted,
``adjust`` shows it to Claude with the plan, and Claude confirms or moves the type
to where the picture actually left room. The renderer (``layout._front_picture``)
sets whatever comes back as vector outlines; nothing here is painted.

Locations:
- top, bottom: author, title and genre stacked across the upper or lower third.
- left, right: the same stack in a narrow column down that side.
- diagonal: the title on a rising baseline across the middle; author and genre
  stay horizontal, above and below it.
"""

import asyncio
import base64
import io
import logging
from functools import cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field, create_model

from . import settings
from .palettes import HEX
from .typography import TYPE_FAMILIES, describe

log = logging.getLogger("covers.lettering")

Location = Literal["top", "bottom", "left", "right", "diagonal"]
Hex = Annotated[str, Field(pattern=HEX)]

#: The rising baseline of a diagonal title, in degrees (negative: rises to the right).
MIN_ANGLE, MAX_ANGLE = -35, -12
#: The long edge of the picture Claude is shown.
PREVIEW_PX = 1024


class Ink(BaseModel):
    """A colour for type: solid, or a two-stop linear gradient."""

    color: Hex = Field(description="The colour, hex; the gradient's first stop.")
    gradient_to: Hex | None = Field(
        default=None, description="The gradient's second stop, hex; null for a solid colour."
    )
    gradient: Literal["down", "across", "diagonal"] = Field(
        default="down", description="Gradient direction: down the lines, across them, or corner to corner."
    )

    @property
    def stops(self) -> tuple[str, ...]:
        return (self.color, self.gradient_to) if self.gradient_to else (self.color,)


class Lettering(BaseModel):
    """How the vector typesetting places the author, title and genre on the picture."""

    location: Location = Field(
        default="top",
        description=(
            "Where the type sits. top/bottom: stacked across the upper or lower third. "
            "left/right: a narrow column down that side, flush to its edge. diagonal: the "
            "title on a rising baseline across the middle, author above and genre below "
            "set horizontally."
        ),
    )
    align: Literal["left", "center", "right"] = Field(
        default="center", description="top/bottom only: how the lines align. Columns align to their edge."
    )
    size: Literal["small", "medium", "large", "dominant"] = Field(
        default="medium",
        description="How much of the cover the title takes: small is quiet, dominant makes the type the picture.",
    )
    angle: int = Field(
        default=-20, ge=MIN_ANGLE, le=MAX_ANGLE,
        description=f"diagonal only: the title's baseline angle in degrees, {MIN_ANGLE} to {MAX_ANGLE}.",
    )
    title_ink: Ink = Field(
        default_factory=lambda: Ink(color="#161412"),
        description="The title's colour. It must read on the picture where the title sits.",
    )
    text_ink: Ink = Field(
        default_factory=lambda: Ink(color="#161412"),
        description="Author and genre line; usually solid, and quieter than the title.",
    )


#: What the image prompt says, per location, about the room the type needs.
_ZONES = {
    "top": "across the upper third, full width",
    "bottom": "across the lower third, full width",
    "left": "in a tall column down the left side, about two fifths of the width",
    "right": "in a tall column down the right side, about two fifths of the width",
    "diagonal": "along a band rising from lower left to upper right through the middle",
}


def zone_text(lettering: Lettering) -> str:
    """The image prompt's instruction to leave room for the type."""
    return (
        f"the author, title and genre will be set directly onto the picture {_ZONES[lettering.location]}, "
        "with nothing behind them: keep that area calm and even -- open sky, water, mist, a plain "
        "wall, soft shadow -- with no objects, faces or busy detail in it, and the subject in the "
        "rest of the frame; a small publisher line sits at the bottom edge"
    )


class Placement(Lettering):
    """The lettering as Claude sets it after seeing the painted picture."""

    type_family: str
    title_case: Literal["upper", "title", "as_is"]
    note: str = Field(description="One line: why the type sits there and reads in that colour.")


@cache
def placement_schema(families: tuple[str, ...] = TYPE_FAMILIES) -> type[Placement]:
    """``Placement`` with the face narrowed to the reader type's families."""
    return create_model(
        "Placement",
        __base__=Placement,
        type_family=(Literal[families], Field(description=describe(families))),
    )


_SYSTEM = """\
You are the typographer for a German trade publisher. You receive a painted cover \
picture and the art director's plan for the type that will be set on it as vector \
outlines: author, title, genre line, and a small imprint at the foot.

Look at where the picture actually left room. Keep the plan if the planned area is \
calm and the type will read there; otherwise move the type to where it will -- the \
calmest area, never across a face or the subject. Choose the face from the families \
offered, to suit this picture and the book. Pick colours from the picture that read \
clearly on the pixels under the type; a two-stop gradient is welcome where it echoes \
the light in the picture, but never at the cost of legibility. Blackletter faces are \
never set in capitals."""


def _preview(image) -> str:
    img = image.convert("RGB")
    img.thumbnail((PREVIEW_PX, PREVIEW_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


async def adjust(
    image, planned: Lettering, *, title: str, author: str, genre: str,
    type_family: str, title_case: str, families: tuple[str, ...] = TYPE_FAMILIES,
) -> Placement | None:
    """The lettering, confirmed or moved after a look at the picture; None if Claude
    is unavailable, and the plan stands."""
    import anthropic

    from .core import client

    if (claude := client()) is None:
        return None
    plan = {**planned.model_dump(), "type_family": type_family, "title_case": title_case}
    text = (
        f"Author: {author}\nTitle: {title}\nGenre line: {genre}\n\n"
        f"The art director's plan:\n{plan}"
    )
    try:
        response = await claude.messages.parse(
            model=settings.claude_model(),
            max_tokens=8000,
            system=_SYSTEM,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": await asyncio.to_thread(_preview, image),
                }},
                {"type": "text", "text": text},
            ]}],
            output_format=placement_schema(families),
        )
    except (anthropic.APIError, TypeError, ValueError) as exc:
        log.warning("lettering adjustment failed (%s)", exc)
        return None
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output
