"""Art direction: turn a text prompt into a concrete cover brief.

This is the judgement step -- palette, layout, typographic register, the German
genre line, and the prompt the image model will render. It
runs on Claude. Image generation itself lives in :mod:`covers.imagegen` and runs
on OpenAI.

If no Anthropic credentials are reachable, or the call fails, a deterministic
brief is derived from a hash of the input so the endpoint still returns a cover.
"""

import hashlib
import logging
import re
from typing import Literal, get_args

from pydantic import BaseModel, Field, model_validator

from . import settings
from .palettes import PALETTES_BY_KEY, Palette, choose_palette
from .typography import FAMILIES

log = logging.getLogger("covers.artdirection")

Template = Literal[
    "rororo_band",
    "kiwi_flat",
    "type_block",
    "didone_centre",
    "photo_duotone",
    "illustrated_full",
]
TEMPLATES: tuple[str, ...] = get_args(Template)

TypeFamily = Literal[
    "geometric",
    "grotesk",
    "grotesk_condensed",
    "neoclassical",
    "didone",
    "literary_serif",
    "humanist",
    "slab",
    "garalde",
    "fraktur",
    "meta",
]

Motif = Literal["arcs", "blocks", "dots", "split", "waveform", "rings", "none"]
MOTIFS: tuple[str, ...] = get_args(Motif)
#: Every motif that actually draws something.
DRAWN_MOTIFS: tuple[str, ...] = tuple(m for m in MOTIFS if m != "none")

Artwork = Literal["generated", "procedural", "none"]

# The Literal has to be spelled out for Pydantic; keep it honest against the
# faces typography actually offers.
assert set(FAMILIES) == set(get_args(TypeFamily))

#: The face each layout is designed around, used when a caller pins the template
#: but not the typeface.
TEMPLATE_DEFAULT_FAMILY: dict[str, str] = {
    "rororo_band": "geometric",
    "kiwi_flat": "grotesk_condensed",
    "type_block": "grotesk",
    "didone_centre": "neoclassical",
    "photo_duotone": "geometric",
    "illustrated_full": "humanist",
}

#: Templates the fallback brief picks from, per register.
STYLE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "illustrated": ("illustrated_full", "photo_duotone"),
    "painterly": ("photo_duotone", "illustrated_full", "didone_centre"),
    "typographic": ("type_block", "kiwi_flat", "rororo_band", "didone_centre"),
}

HEX = r"^#[0-9A-Fa-f]{6}$"

#: How much of the input text is shown to the model.
MAX_PROMPT_CHARS = 24_000


class ArtDirection(BaseModel):
    """The cover brief.

    Mostly design decisions the renderer consumes. ``genre_line`` is book copy
    rather than design: the book record owns it, and a caller may pin it. ``keywords`` is emitted but read by nothing; it earns its
    place by making the model characterise the book before it picks a layout.
    """

    mood: str = Field(description="The cover's register in three to six German words.")
    keywords: list[str] = Field(
        description="Three to six German keywords describing the book's subject and tone."
    )
    template: Template = Field(
        description=(
            "Layout. illustrated_full: the picture runs across the whole cover and "
            "the type sits in a panel over it -- the layout for an illustrated cover. "
            "photo_duotone: artwork across the upper two thirds, type below. "
            "rororo_band: full-bleed ground with a horizontal band holding the title. "
            "kiwi_flat: flat ground, left-aligned type stack in the upper half. "
            "type_block: display type filling the whole cover edge to edge, no imagery. "
            "didone_centre: centred neoclassical setting with hairline rules."
        )
    )
    type_family: TypeFamily = Field(
        description=(
            "geometric is Futura (the rororo/KiWi workhorse), grotesk is Helvetica Neue, "
            "grotesk_condensed is a tall condensed sans, neoclassical is Didot, didone is "
            "Bodoni, literary_serif is Baskerville, humanist is Optima, slab is a Clarendon. "
            "garalde is Garamond, the classic literary-fiction face of Suhrkamp, Insel "
            "and Hanser. fraktur is blackletter for the title, for fairy tales, legends "
            "and historical subjects; never set it in capitals. meta is Spiekermann's "
            "humanist sans, contemporary and non-fiction in feel."
        )
    )
    artwork: Artwork = Field(
        description=(
            "generated: have the image model paint artwork. This is the normal "
            "choice. procedural: an abstract vector motif, for covers that want a "
            "constructed geometric mark rather than a painted image. none: pure "
            "typography -- reserve it for when the type genuinely carries the cover "
            "alone, not as a default."
        )
    )
    motif: Motif = Field(
        description="Vector motif, used when artwork is 'procedural'; 'none' otherwise."
    )
    ground: str = Field(pattern=HEX, description="Background colour, hex.")
    ink: str = Field(pattern=HEX, description="Primary type colour, must read on the ground.")
    accent: str = Field(pattern=HEX, description="Accent for bands, rules and the genre line.")
    secondary: str = Field(pattern=HEX, description="Supporting colour for motifs.")
    title_case: Literal["upper", "title", "as_is"] = Field(
        description="How to case the title. Uppercase suits geometric and grotesk display."
    )
    genre_line: str = Field(
        description=(
            "German Gattungsbezeichnung printed under the title, e.g. 'Roman', "
            "'Erzählungen', 'Essays', 'Gedichte', 'Novelle'. Keep it to one or two words."
        )
    )
    image_prompt: str = Field(
        description=(
            "Prompt for the image model, in English, in the register the system "
            "prompt sets. Be concrete and specific -- name the subject, the medium, "
            "the light, the season, one or two telling details. A vague prompt "
            "returns a generic wash. Two to four sentences. It must contain no "
            "lettering, no words and no book-cover furniture of any kind, because "
            "the type is set separately on top of it."
        )
    )
    rationale: str = Field(description="One or two sentences on why this cover fits the book.")

    @model_validator(mode="after")
    def _no_blackletter_capitals(self) -> "ArtDirection":
        # Blackletter capitals are not meant to stand in a row; a title in them is
        # unreadable. Holds whoever chose the casing, model or caller.
        if self.type_family == "fraktur" and self.title_case == "upper":
            self.title_case = "title"
        return self

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


_SYSTEM_BASE = """\
You are the art director for a German literary publisher in the tradition of \
Kiepenheuer & Witsch and Rowohlt. You brief covers for the German trade market.

Always true, whatever the register:
- The title is the largest element. The author's name is set smaller, usually \
letterspaced, above or below the title.
- A Gattungsbezeichnung ("Roman", "Erzählungen", "Essays") is printed under the \
title. This is expected on German literary covers; choose the right one.
- Pick colours that give the ink real contrast against the ground. A cover nobody \
can read at thumbnail size has failed.
- You never put lettering in the image prompt. The type is set separately, in \
vector, on top of whatever the image model returns; an image with letters in it is \
unusable. Describe the picture, not the cover.
- Write the genre line in German even when the input is in another language.
"""

#: The register the cover is briefed in. This is the caller's choice, not yours --
#: each one is a legitimate strand of the German trade market, and the house style
#: should not be smuggled in as a default.
STYLE_GUIDANCE: dict[str, str] = {
    "illustrated": """\
Register for this cover: ILLUSTRATED AND CHARMING.

- The picture carries the cover and it is a drawn illustration: gouache, coloured \
pencil, ink and wash, or cut paper. Visible hand, visible texture, a little \
imperfect.
- It is figurative and specific. Show the thing: the house, the harbour, the two \
sisters at the kitchen table, the dog on the stairs, the pear tree in a courtyard. \
A reader should be able to say what is in the picture. Warmth and wit are wanted; \
so is a small telling detail.
- Colour is generous. Four, six, eight colours, in a scheme that feels mixed by \
hand rather than picked from a system. Sunlight, weather, time of day.
- Prefer `template: "illustrated_full"`, which runs the illustration across the \
whole cover and sets the type in a panel over it.
- Set `artwork: "generated"`. An illustrated cover without an illustration is \
nothing.
- What to avoid: corporate flat vector, gradient mesh, 3D rendering, stock \
photography, anything that looks like an app icon.\
""",
    "painterly": """\
Register for this cover: PAINTERLY AND LITERARY.

- The picture is a painting: oil or gouache, real brushwork, atmosphere over \
outline. A landscape, an interior, a figure seen from behind, a still life.
- Figurative but unhurried -- it can suggest the book's world rather than state its \
plot. Mood carries more than incident.
- Colour is full and tonal, not restricted to a flat scheme. Let light do the work.
- `template: "photo_duotone"` or `"illustrated_full"` both suit this. Set \
`artwork: "generated"`.\
""",
    "typographic": """\
Register for this cover: TYPOGRAPHIC AND AUSTERE.

- Typography carries the cover. Flat colour grounds, one strong display face, \
generous white space. Decoration is suspect.
- Colour is flat and confident: two or three colours, not five. No gradients, no \
drop shadows.
- Imagery, if any, is abstract -- a geometric mark or a composition reduced to a \
few planes. Never a literal illustration of the plot.
- `artwork: "none"` or `"procedural"` are both right here. `type_block` and \
`kiwi_flat` are the layouts for it.\
""",
}

STYLES: tuple[str, ...] = tuple(STYLE_GUIDANCE)
DEFAULT_STYLE = "illustrated"


def system_prompt(style: str = DEFAULT_STYLE) -> str:
    guidance = STYLE_GUIDANCE.get(style, STYLE_GUIDANCE[DEFAULT_STYLE])
    return f"{_SYSTEM_BASE}\n{guidance}"


def clip_prompt(text: str) -> tuple[str, bool]:
    """Bound the prompt, keeping the opening and the ending if it is long."""
    if len(text) <= MAX_PROMPT_CHARS:
        return text, False
    head = text[: int(MAX_PROMPT_CHARS * 0.7)]
    tail = text[-int(MAX_PROMPT_CHARS * 0.3) :]
    return f"{head}\n\n[…]\n\n{tail}", True


def user_prompt(title: str, author: str, text: str) -> str:
    """The message both directors send, so their briefs stay comparable."""
    return (
        f"Titel: {title}\n"
        f"Autor/in: {author}\n\n"
        f"Vorgabe/Text:\n{text}\n\n"
        "Brief den Umschlag."
    )


def degrade(
    meta: dict, reason: str, text: str, title: str, author: str, style: str
) -> tuple[ArtDirection, dict]:
    """Fall back to the deterministic brief, recording why."""
    meta.update(source="fallback", reason=reason)
    return fallback_direction(text, title, author, style), meta


def seed_from(*parts: str) -> int:
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


def _genre_from_text(lowered: str) -> str:
    """Guess the Gattungsbezeichnung, used only when no model wrote the brief.

    Matched as a whole word plus a short inflectional tail, not as a substring.
    Substring matching reads "verschiedene" as the poetry term "vers" and prints
    "Gedichte" on a novel -- German is too compound-happy for `in`.
    """
    for needles, label in _GENRE_HINTS:
        for needle in needles:
            if re.search(rf"\b{re.escape(needle)}\w{{0,3}}\b", lowered):
                return label
    return "Roman"


#: Fallback image prompts per register. Deliberately concrete -- a vague prompt is
#: what produces the generic abstract wash.
_FALLBACK_IMAGE_PROMPT = {
    "illustrated": (
        "A warm figurative gouache illustration of a small harbour town at "
        "mid-afternoon: pitched roofs, a washing line, a cat asleep on a wall, one "
        "boat drawn up on the mud. Visible brush and pencil texture, generous "
        "hand-mixed colour, a little imperfect."
    ),
    "painterly": (
        "An oil painting of a wide coastal landscape in changing weather, seen from "
        "a low bank: pale sand, shallow water catching the light, a heavy sky. Real "
        "brushwork, atmospheric, tonal rather than flat."
    ),
    "typographic": (
        "An abstract geometric composition of a few flat overlapping planes, matte "
        "gouache texture, no depth, no lettering."
    ),
}


def fallback_direction(
    text: str, title: str, author: str, style: str = DEFAULT_STYLE
) -> ArtDirection:
    """Deterministic brief, used when the model is unavailable."""
    seed = seed_from(text, title, author, style)
    lowered = f"{title} {text}".lower()

    genre = _genre_from_text(lowered)

    # Warm palettes for the illustrated register, the full set otherwise.
    tone = ("charmant", "warm", "heiter") if style == "illustrated" else ()
    palette = choose_palette(seed, tone)
    candidates = STYLE_TEMPLATES.get(style, TEMPLATES)
    template = candidates[(seed >> 8) % len(candidates)]
    family = TEMPLATE_DEFAULT_FAMILY[template]
    motif = DRAWN_MOTIFS[(seed >> 16) % len(DRAWN_MOTIFS)]

    return ArtDirection(
        mood=", ".join(palette.tone[:3]) or "sachlich",
        keywords=list(palette.tone[:4]) or ["literatur"],
        template=template,  # type: ignore[arg-type]
        type_family=family,  # type: ignore[arg-type]
        # Ask for a picture by default. `type_block` has no room for one, so it
        # falls through to pure typography and the pipeline says so in `notes`.
        artwork="none" if template == "type_block" else "generated",
        motif=motif,  # type: ignore[arg-type]
        ground=palette.ground,
        ink=palette.ink,
        accent=palette.accent,
        secondary=palette.secondary,
        title_case="upper" if family in ("geometric", "grotesk", "grotesk_condensed") else "title",
        genre_line=genre,
        image_prompt=_FALLBACK_IMAGE_PROMPT.get(
            style, _FALLBACK_IMAGE_PROMPT[DEFAULT_STYLE]
        ),
        rationale="Deterministic fallback brief: no art-direction model was reachable.",
    )


async def direct(
    text: str,
    title: str,
    author: str,
    *,
    style: str = DEFAULT_STYLE,
) -> tuple[ArtDirection, dict]:
    """Produce a cover brief. Returns the brief and metadata about how it was made."""
    model = settings.claude_model()
    meta: dict = {"source": "claude", "model": model, "style": style}
    prompt_text, clipped = clip_prompt(text)
    meta["input_clipped"] = clipped

    # Imported here, not at module scope: ArtDirection is the renderer's type
    # vocabulary, and `covers.layout` must not pull the Anthropic SDK to use it.
    import anthropic

    try:
        ac = anthropic.AsyncAnthropic()
    except Exception as exc:  # no credentials resolvable
        log.info("art direction falling back: %s", exc)
        return degrade(meta, str(exc), text, title, author, style)

    user = user_prompt(title, author, prompt_text)

    try:
        response = await ac.messages.parse(
            model=model,
            max_tokens=8000,
            system=system_prompt(style),
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": user}],
            output_format=ArtDirection,
        )
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "category", None)
            log.warning("art direction refused (%s), using fallback", detail)
            return degrade(meta, f"refusal:{detail}", text, title, author, style)
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
        return degrade(meta, str(exc), text, title, author, style)


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
    """Let the caller pin any part of the brief.

    Copy the caller pins lands on the brief, not just on the rendered content, so
    the brief stays the single source of truth for what the cover says.
    """
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
