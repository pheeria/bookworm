"""Art direction: turn a text prompt into a concrete cover brief.

This is the judgement step -- palette, layout, typographic register, the German
genre line, the back-cover copy, and the prompt the image model will render. It
runs on Claude. Image generation itself lives in :mod:`bookworm.imagegen` and runs
on OpenAI.

If no Anthropic credentials are reachable, or the call fails, a deterministic
brief is derived from a hash of the input so the endpoint still returns a cover.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .palettes import PALETTES_BY_KEY, Palette, choose_palette
from .typography import FAMILIES

log = logging.getLogger("bookworm.artdirection")

MODEL = os.environ.get("BOOKWORM_CLAUDE_MODEL", "claude-opus-5")

Template = Literal[
    "rororo_band",
    "kiwi_flat",
    "type_block",
    "didone_centre",
    "photo_duotone",
]
TEMPLATES: tuple[str, ...] = (
    "rororo_band",
    "kiwi_flat",
    "type_block",
    "didone_centre",
    "photo_duotone",
)

TypeFamily = Literal[
    "geometric",
    "grotesk",
    "grotesk_condensed",
    "neoclassical",
    "didone",
    "literary_serif",
    "humanist",
    "slab",
]

Motif = Literal["arcs", "blocks", "dots", "split", "waveform", "rings", "none"]
MOTIFS: tuple[str, ...] = ("arcs", "blocks", "dots", "split", "waveform", "rings", "none")

Artwork = Literal["generated", "procedural", "none"]

# The Literal above has to be static for Pydantic; keep it honest.
assert set(FAMILIES) == {
    "geometric",
    "grotesk",
    "grotesk_condensed",
    "neoclassical",
    "didone",
    "literary_serif",
    "humanist",
    "slab",
}

#: The face each layout is designed around, used when a caller pins the template
#: but not the typeface.
TEMPLATE_DEFAULT_FAMILY: dict[str, str] = {
    "rororo_band": "geometric",
    "kiwi_flat": "grotesk_condensed",
    "type_block": "grotesk",
    "didone_centre": "neoclassical",
    "photo_duotone": "geometric",
}

HEX = r"^#[0-9A-Fa-f]{6}$"

#: How much of the input text is shown to the model.
MAX_PROMPT_CHARS = 24_000


class ArtDirection(BaseModel):
    """The cover brief. Every field is consumed by the renderer."""

    mood: str = Field(description="The cover's register in three to six German words.")
    keywords: list[str] = Field(
        description="Three to six German keywords describing the book's subject and tone."
    )
    template: Template = Field(
        description=(
            "Layout. rororo_band: full-bleed ground with a horizontal band holding the "
            "title. kiwi_flat: flat ground, left-aligned type stack in the upper half. "
            "type_block: display type filling the whole cover edge to edge, no imagery. "
            "didone_centre: centred neoclassical setting with hairline rules. "
            "photo_duotone: artwork across the upper two thirds, type below."
        )
    )
    type_family: TypeFamily = Field(
        description=(
            "geometric is Futura (the rororo/KiWi workhorse), grotesk is Helvetica Neue, "
            "grotesk_condensed is a tall condensed sans, neoclassical is Didot, didone is "
            "Bodoni, literary_serif is Baskerville, humanist is Optima, slab is a Clarendon."
        )
    )
    artwork: Artwork = Field(
        description=(
            "generated: have the image model paint artwork. procedural: an abstract "
            "vector motif. none: pure typography, which is idiomatic for this market."
        )
    )
    motif: Motif = Field(
        description="Vector motif, used when artwork is 'procedural'; 'none' otherwise."
    )
    ground: str = Field(pattern=HEX, description="Background colour, hex.")
    ink: str = Field(pattern=HEX, description="Primary type colour, must read on the ground.")
    accent: str = Field(pattern=HEX, description="Accent for bands, rules and the genre line.")
    secondary: str = Field(pattern=HEX, description="Supporting colour for motifs and the spine.")
    title_case: Literal["upper", "title", "as_is"] = Field(
        description="How to case the title. Uppercase suits geometric and grotesk display."
    )
    genre_line: str = Field(
        description=(
            "German Gattungsbezeichnung printed under the title, e.g. 'Roman', "
            "'Erzählungen', 'Essays', 'Gedichte', 'Novelle'. Keep it to one or two words."
        )
    )
    blurb: str = Field(
        description=(
            "German back-cover copy: two to four sentences, present tense, no spoilers, "
            "no quotation marks, no exclamation marks."
        )
    )
    image_prompt: str = Field(
        description=(
            "Prompt for the image model, in English. Describe an abstract or "
            "semi-figurative painted image in the palette above. It must contain no "
            "lettering, no words and no book-cover furniture of any kind, because the "
            "type is set separately on top of it."
        )
    )
    rationale: str = Field(description="One or two sentences on why this cover fits the book.")

    @property
    def palette(self) -> Palette:
        return Palette(
            key="model",
            label=self.mood,
            ground=self.ground,
            ink=self.ink,
            accent=self.accent,
            secondary=self.secondary,
        )


SYSTEM = """\
You are the art director for a German literary publisher in the tradition of \
Kiepenheuer & Witsch and Rowohlt. You brief covers for the German trade market.

House idiom, which you follow:
- Typography carries the cover. Flat colour grounds, one strong display face, \
generous white space. Decoration is suspect.
- The title is the largest element. The author's name is set smaller, usually \
letterspaced, above or below the title.
- A Gattungsbezeichnung ("Roman", "Erzählungen", "Essays") is printed under the \
title. This is expected on German literary covers; choose the right one.
- Colour is flat, confident and a little austere. Two or three colours, not five. \
Gradients and drop shadows are not part of this idiom.
- Imagery, when it appears at all, is abstract, painterly or a single arresting \
object. Never a literal illustration of the plot, never stock photography.

You never put lettering in the image prompt: the type is set separately, in vector, \
on top of whatever the image model returns. An image with letters in it is unusable.

Choose colours that give the ink real contrast against the ground. Write the blurb \
and genre line in German even when the input is in another language.\
"""


def _clip(text: str) -> tuple[str, bool]:
    """Bound the prompt, keeping the opening and the ending if it is long."""
    if len(text) <= MAX_PROMPT_CHARS:
        return text, False
    head = text[: int(MAX_PROMPT_CHARS * 0.7)]
    tail = text[-int(MAX_PROMPT_CHARS * 0.3) :]
    return f"{head}\n\n[…]\n\n{tail}", True


def _seed(*parts: str) -> int:
    digest = hashlib.blake2b("\x1f".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


_GENRE_HINTS = (
    (("gedicht", "lyrik", "poem", "vers"), "Gedichte"),
    (("erzählung", "geschichten", "stories", "kurzgeschicht"), "Erzählungen"),
    (("essay", "aufsätze", "kritik"), "Essays"),
    (("novelle", "novella"), "Novelle"),
    (("memoir", "erinnerungen", "autobiograf", "autobiograph"), "Erinnerungen"),
    (("biografie", "biographie", "leben des"), "Biographie"),
    (("märchen", "fairy"), "Märchen"),
    (("bericht", "reportage", "sachbuch"), "Ein Bericht"),
)


def fallback_direction(text: str, title: str, author: str) -> ArtDirection:
    """Deterministic brief, used when the model is unavailable."""
    seed = _seed(text, title, author)
    lowered = f"{title} {text}".lower()

    genre = "Roman"
    for needles, label in _GENRE_HINTS:
        if any(n in lowered for n in needles):
            genre = label
            break

    palette = choose_palette(seed)
    template = TEMPLATES[(seed >> 8) % len(TEMPLATES)]
    family = TEMPLATE_DEFAULT_FAMILY[template]
    motif = MOTIFS[(seed >> 16) % (len(MOTIFS) - 1)]

    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    blurb = ". ".join(sentences[:3])
    blurb = (blurb + ".") if blurb else f"{title} von {author}."

    return ArtDirection(
        mood=", ".join(palette.tone[:3]) or "sachlich",
        keywords=list(palette.tone[:4]) or ["literatur"],
        template=template,  # type: ignore[arg-type]
        type_family=family,  # type: ignore[arg-type]
        artwork="procedural" if template == "photo_duotone" else "none",
        motif=motif,  # type: ignore[arg-type]
        ground=palette.ground,
        ink=palette.ink,
        accent=palette.accent,
        secondary=palette.secondary,
        title_case="upper" if family in ("geometric", "grotesk", "grotesk_condensed") else "title",
        genre_line=genre,
        blurb=blurb[:600],
        image_prompt=(
            f"Abstract painted composition in {palette.label.lower()} tones, "
            "flat shapes, matte gouache texture, no lettering."
        ),
        rationale="Deterministic fallback brief: no art-direction model was reachable.",
    )


async def direct(
    text: str,
    title: str,
    author: str,
    *,
    client: anthropic.AsyncAnthropic | None = None,
) -> tuple[ArtDirection, dict]:
    """Produce a cover brief. Returns the brief and metadata about how it was made."""
    meta: dict = {"source": "claude", "model": MODEL}
    prompt_text, clipped = _clip(text)
    meta["input_clipped"] = clipped

    try:
        ac = client or anthropic.AsyncAnthropic()
    except Exception as exc:  # no credentials resolvable
        log.info("art direction falling back: %s", exc)
        meta.update(source="fallback", reason=str(exc))
        return fallback_direction(text, title, author), meta

    user = (
        f"Titel: {title}\n"
        f"Autor/in: {author}\n\n"
        f"Vorgabe/Text:\n{prompt_text}\n\n"
        "Brief den Umschlag."
    )

    try:
        response = await ac.messages.parse(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": user}],
            output_format=ArtDirection,
        )
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "category", None)
            log.warning("art direction refused (%s), using fallback", detail)
            meta.update(source="fallback", reason=f"refusal:{detail}")
            return fallback_direction(text, title, author), meta
        direction = response.parsed_output
        if direction is None:
            raise ValueError("model returned no parsed output")
        meta["usage"] = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return direction, meta
    except (anthropic.APIError, ValueError, TypeError) as exc:
        log.warning("art direction failed (%s), using fallback", exc)
        meta.update(source="fallback", reason=str(exc))
        return fallback_direction(text, title, author), meta


def apply_overrides(
    direction: ArtDirection,
    *,
    template: str | None = None,
    type_family: str | None = None,
    palette_key: str | None = None,
    artwork: str | None = None,
    motif: str | None = None,
    genre_line: str | None = None,
) -> ArtDirection:
    """Let the caller pin any part of the brief."""
    data = direction.model_dump()
    if template:
        data["template"] = template
        # Each layout is drawn around a particular face; pinning the template
        # without a typeface should move the typeface with it.
        if not type_family:
            data["type_family"] = TEMPLATE_DEFAULT_FAMILY.get(
                template, data["type_family"]
            )
    if type_family:
        data["type_family"] = type_family
    if artwork:
        data["artwork"] = artwork
    if motif:
        data["motif"] = motif
    if genre_line:
        data["genre_line"] = genre_line
    if palette_key:
        try:
            p = PALETTES_BY_KEY[palette_key]
        except KeyError:
            raise KeyError(
                f"unknown palette {palette_key!r}; available: "
                f"{', '.join(sorted(PALETTES_BY_KEY))}"
            ) from None
        data.update(ground=p.ground, ink=p.ink, accent=p.accent, secondary=p.secondary)
    return ArtDirection.model_validate(data)
