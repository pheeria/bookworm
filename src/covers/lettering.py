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

import base64
from functools import cache
from typing import TYPE_CHECKING, Annotated, Literal

from PIL import ImageOps
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    create_model,
    field_validator,
    model_validator,
)

from . import imagethread
from .core import parse
from .palettes import HEX
from .typography import describe

if TYPE_CHECKING:
    from .artdirection import ArtDirection

Location = Literal["top", "bottom", "left", "right", "diagonal"]
def _long_hex(value):
    """``#abc`` as ``#aabbcc``: a short hex from the model is not worth failing over."""
    if isinstance(value, str) and len(value) == 4 and value.startswith("#"):
        return "#" + "".join(c * 2 for c in value[1:])
    return value


Hex = Annotated[str, BeforeValidator(_long_hex), Field(pattern=HEX)]
TitleCase = Literal["upper", "title", "as_is"]

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
    author_location: Literal["with_title", "top", "bottom"] = Field(
        default="with_title",
        description=(
            "Where the author's name stands: with_title, just above the title; or apart, "
            "on its own along the top or the bottom edge -- title at the top and author "
            "at the bottom, say."
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
    @field_validator("angle", mode="before")
    @classmethod
    def _clamp_angle(cls, value):
        # Only a diagonal reads it, so an angle out of range elsewhere (0 for
        # "horizontal", say) must not fail the whole answer.
        return max(MIN_ANGLE, min(MAX_ANGLE, value)) if isinstance(value, int | float) else value

    @model_validator(mode="after")
    def _author_apart_only_where_free(self) -> "Lettering":
        # Apart from the title only at the edge it leaves free; a diagonal title
        # takes the middle, so its author always stands at an edge.
        title_edge = {"top": "top", "left": "top", "right": "top", "bottom": "bottom"}.get(self.location)
        if self.author_location == title_edge:
            self.author_location = "with_title"
        elif self.location == "diagonal" and self.author_location == "with_title":
            self.author_location = "top"
        return self

    title_ink: Ink | None = Field(
        default=None,
        description="The title's colour; it must read on the picture where the title sits. Null: the brief's ink.",
    )
    text_ink: Ink | None = Field(
        default=None,
        description="Author and genre line; usually solid, and quieter than the title. Null: the brief's ink.",
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
    zone = _ZONES[lettering.location]
    if lettering.author_location == "with_title":
        where = f"the author, title and genre will be set directly onto the picture {zone}"
    else:
        where = (f"the title and genre will be set directly onto the picture {zone}, and the "
                 f"author's name along the {lettering.author_location} edge")
    return (
        f"{where}, with nothing behind them: keep those areas calm and even -- open sky, water, "
        "mist, a plain wall, soft shadow -- with no objects, faces or busy detail in them, and the "
        "subject in the rest of the frame; a small publisher line sits at the bottom edge"
    )


class Placement(BaseModel):
    """The lettering and face as Claude sets them after seeing the painted picture."""

    lettering: Lettering
    type_family: str
    title_case: TitleCase
    note: str = Field(description="One line: why the type sits there and reads in that colour.")


@cache
def placement_schema(families: tuple[str, ...]) -> type[Placement]:
    """``Placement`` with the face narrowed to the families allowed."""
    return create_model(
        "Placement",
        __base__=Placement,
        type_family=(Literal[families], Field(description=describe(families))),
    )


_SYSTEM = """\
You are the typographer for a German trade publisher. You receive a painted cover \
picture and the art director's plan for the type that will be set on it as vector \
outlines: author, title, genre line, and a small imprint at the foot.

Look at where the picture actually left room. Keep the plan if the planned areas are \
calm and the type will read there; otherwise move the type to where it will -- the \
calmest area, never across a face or the subject. The author may stand with the \
title or apart, along the top or bottom edge, wherever the picture is quietest. \
Choose the face from the families offered, to suit this picture and the book. Pick \
colours with strong contrast to the pixels under the type, light on dark or dark on \
light; a two-stop gradient is welcome where it echoes the light in the picture, but \
never at the cost of legibility. Blackletter faces are never set in capitals."""


def _preview(image) -> str:
    # Late: imagegen imports artdirection, which imports this module.
    from .imagegen import to_jpeg

    # Shrink first: the painted picture is print-size, Claude needs a glance.
    small = ImageOps.contain(image, (PREVIEW_PX, PREVIEW_PX)).convert("RGB")
    return base64.b64encode(to_jpeg(small, quality=85)).decode("ascii")


async def adjust(
    image, direction: "ArtDirection", *, title: str, author: str, families: tuple[str, ...]
) -> Placement | None:
    """The brief's lettering and face, confirmed or moved after a look at the
    picture, the face one of ``families``; None if Claude is unavailable, and the
    plan stands."""
    plan = direction.model_dump(include={"lettering", "type_family", "title_case"})
    text = (
        f"Author: {author}\nTitle: {title}\nGenre line: {direction.genre_line}\n\n"
        f"The art director's plan:\n{plan}"
    )
    return await parse(
        placement_schema(families), system=_SYSTEM, max_tokens=8000, what="lettering adjustment",
        content=[
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": await imagethread.run(_preview, image),
            }},
            {"type": "text", "text": text},
        ],
    )
