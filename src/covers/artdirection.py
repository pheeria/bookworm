"""Art direction: turn a text prompt into a concrete cover brief.

This is the judgement step -- palette, layout, typographic register, the German
genre line, and the prompt the image model will render. It runs on Claude here;
:mod:`covers.director_openai` is the same brief from OpenAI. Image generation
itself lives in :mod:`covers.imagegen`.

If no Anthropic credentials are reachable, or the call fails, a deterministic
brief is derived from a hash of the input so the endpoint still returns a cover.
"""

import hashlib
import logging
import re
from functools import cache
from typing import Literal, get_args

from pydantic import BaseModel, Field, create_model, model_validator

from . import motifs, settings
from .lettering import Lettering, TitleCase
from .moods import MOODS, Mood
from .palettes import (
    HEX,
    PALETTE_KEYS,
    PALETTES_BY_KEY,
    Palette,
    choose_palette,
    describe_palettes,
)
from .typography import FAMILIES, TYPE_FAMILIES, describe

log = logging.getLogger("covers.artdirection")

Template = Literal[
    "rororo_band",
    "kiwi_flat",
    "type_block",
    "didone_centre",
    "photo_duotone",
    "illustrated_full",
    "picture",
]
TEMPLATES: tuple[str, ...] = get_args(Template)

TypeFamily = Literal[TYPE_FAMILIES]  # type: ignore[valid-type]

MOTIFS: tuple[str, ...] = tuple(motifs.MOTIFS)
Motif = Literal[MOTIFS]  # type: ignore[valid-type]
#: Every motif that actually draws something.
DRAWN_MOTIFS: tuple[str, ...] = tuple(m for m in MOTIFS if m != "none")

Artwork = Literal["generated", "procedural", "none"]

for _mood in MOODS.values():
    assert set(_mood.templates) <= set(TEMPLATES), _mood.key
    assert set(_mood.type_families) <= set(FAMILIES), _mood.key

#: The face each layout is designed around, used when a caller pins the template
#: but not the typeface.
TEMPLATE_DEFAULT_FAMILY: dict[str, str] = {
    "rororo_band": "geometric",
    "kiwi_flat": "grotesk_condensed",
    "type_block": "grotesk",
    "didone_centre": "neoclassical",
    "photo_duotone": "geometric",
    "illustrated_full": "humanist",
    "picture": "humanist",
}

#: Templates the fallback brief picks from, per register.
STYLE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "illustrated": ("illustrated_full", "photo_duotone"),
    "painterly": ("photo_duotone", "illustrated_full", "didone_centre"),
    "typographic": ("type_block", "kiwi_flat", "rororo_band", "didone_centre"),
}

#: How much of the input text is shown to the model.
MAX_PROMPT_CHARS = 24_000


def family_description(suited: tuple[str, ...] = ()) -> str:
    """What a brief is told about the type families: all of them, and which suit
    its reader type best."""
    text = (
        "Name the register, not a font file; a macOS face named here is set in its "
        "open counterpart where it is not installed. " + describe(TYPE_FAMILIES)
    )
    return f"{text} Suited to this reader type: {', '.join(suited)}." if suited else text


#: What each layout does, for the brief's description of the ones it may choose.
TEMPLATE_DESCRIPTIONS = {
    "illustrated_full": (
        "the picture runs across the whole cover and the type sits in a panel over it "
        "-- the layout for an illustrated cover"
    ),
    "photo_duotone": "artwork across the upper two thirds, type below",
    "rororo_band": "full-bleed ground with a horizontal band holding the title",
    "kiwi_flat": "flat ground, left-aligned type stack in the upper half",
    "type_block": "display type filling the whole cover edge to edge, no imagery",
    "didone_centre": "centred neoclassical setting with hairline rules",
    "picture": (
        "the picture runs across the whole cover and the type is set straight onto it, "
        "with nothing behind the type, in the calm area the picture leaves for it (see lettering)"
    ),
}
assert set(TEMPLATE_DESCRIPTIONS) == set(TEMPLATES)


def _template_description(templates: tuple[str, ...]) -> str:
    return "Layout. " + " ".join(f"{t}: {TEMPLATE_DESCRIPTIONS[t]}." for t in templates)


#: The registers a cover can be briefed in, and what each looks like in the image
#: prompt's words.
STYLE_LOOK: dict[str, str] = {
    "illustrated": (
        "a drawn illustration -- gouache, coloured pencil, ink and wash or cut paper -- "
        "figurative and specific, with visible hand and texture"
    ),
    "painterly": "a painting, oil or gouache with real brushwork, atmosphere over outline, tonal colour",
    "typographic": (
        "abstract and reduced: a few flat planes or one strong sign, flat confident colour, "
        "no literal illustration"
    ),
}
DEFAULT_STYLE = "illustrated"
STYLES: tuple[str, ...] = tuple(STYLE_LOOK)
Style = Literal[STYLES]  # type: ignore[valid-type]
PaletteKey = Literal[PALETTE_KEYS]  # type: ignore[valid-type]


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
    template: Template = Field(description=_template_description(TEMPLATES))
    lettering: Lettering = Field(
        default_factory=Lettering,
        description=(
            "picture only: where and how the author, title and genre are set on the "
            "picture. The image prompt must keep that area calm -- sky, water, a plain wall."
        ),
    )
    type_family: TypeFamily = Field(description=family_description())
    style: Style = Field(
        default=DEFAULT_STYLE,
        description="The register: " + "; ".join(f"{k}: {v}" for k, v in STYLE_LOOK.items()) + ".",
    )
    house_palette: PaletteKey | None = Field(
        default=None,
        description=(
            "A house palette, whose four colours then replace ground, ink, accent and "
            "secondary; null for your own. " + describe_palettes()
        ),
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
    title_case: TitleCase = Field(
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
    def _house_palette(self) -> "ArtDirection":
        if self.house_palette:
            p = PALETTES_BY_KEY[self.house_palette]
            self.ground, self.ink, self.accent, self.secondary = p.ground, p.ink, p.accent, p.secondary
        return self

    @model_validator(mode="after")
    def _no_blackletter_capitals(self) -> "ArtDirection":
        # Blackletter capitals are not meant to stand in a row; a title in them is
        # unreadable. Holds whoever chose the casing, model or caller.
        if FAMILIES[self.type_family].casing == "never_upper" and self.title_case == "upper":
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
- What to avoid: corporate flat vector, gradient mesh, 3D rendering, stock \
photography, anything that looks like an app icon.\
""",
    "painterly": """\
Register for this cover: PAINTERLY AND LITERARY.

- The picture is a painting: oil or gouache, real brushwork, atmosphere over \
outline. A landscape, an interior, a figure seen from behind, a still life.
- Figurative but unhurried -- it can suggest the book's world rather than state its \
plot. Mood carries more than incident.
- Colour is full and tonal, not restricted to a flat scheme. Let light do the work.\
""",
    "typographic": """\
Register for this cover: TYPOGRAPHIC AND AUSTERE.

- Typography carries the cover. Flat colour grounds, one strong display face, \
generous white space. Decoration is suspect.
- Colour is flat and confident: two or three colours, not five. No gradients, no \
drop shadows.
- Imagery, if any, is abstract -- a geometric mark or a composition reduced to a \
few planes. Never a literal illustration of the plot.\
""",
}

#: Layout and artwork advice per register, for a brief that may choose any layout.
#: A mood's brief leaves it out: the mood already decides the layouts it allows.
STYLE_LAYOUT: dict[str, str] = {
    "illustrated": (
        '- Prefer `template: "illustrated_full"`, which runs the illustration across the '
        'whole cover and sets the type in a panel over it.\n- Set `artwork: "generated"`. '
        "An illustrated cover without an illustration is nothing."
    ),
    "painterly": '- `template: "photo_duotone"` or `"illustrated_full"` both suit this. Set `artwork: "generated"`.',
    "typographic": (
        '- `artwork: "none"` or `"procedural"` are both right here. `type_block` and '
        "`kiwi_flat` are the layouts for it."
    ),
}

assert set(STYLE_GUIDANCE) == set(STYLES)



def system_prompt(style: str = DEFAULT_STYLE, mood: Mood | None = None) -> str:
    style = style if style in STYLE_GUIDANCE else DEFAULT_STYLE
    prompt = f"{_SYSTEM_BASE}\n{STYLE_GUIDANCE[style]}"
    if mood:
        # The layouts and faces it allows are in the output schema, not here.
        return f"{prompt}\n\nWho this cover is for: {mood.guidance}"
    return f"{prompt}\n{STYLE_LAYOUT[style]}"


@cache
def brief_schema(mood: Mood | None = None) -> type[ArtDirection]:
    """The structured-output target: the brief, with the mood's layouts and the
    type families that suit it named. Any family may be chosen."""
    if mood is None:
        return ArtDirection
    return create_model(
        f"ArtDirection_{mood.key}",
        __base__=ArtDirection,
        template=(Literal[mood.templates], Field(description=_template_description(mood.templates))),
        type_family=(TypeFamily, Field(description=family_description(mood.type_families))),
    )


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
    meta: dict, reason: str, text: str, title: str, author: str, style: str,
    mood: Mood | None = None,
) -> tuple[ArtDirection, dict]:
    """Fall back to the deterministic brief, recording why."""
    meta.update(source="fallback", reason=reason)
    return fallback_direction(text, title, author, style, mood), meta


def seed_from(*parts: str) -> int:
    """A stable seed for the parts, below 2**63 so it can be pinned and stored."""
    digest = hashlib.blake2b("\x1f".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") >> 1


_GENRE_HINTS = (
    (("gedicht", "lyrik", "poem", "verse"), "Gedichte"),
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


#: How a locally composed image prompt opens, when no model wrote one.
_PROMPT_PREAMBLE = {
    style: f"Cover artwork for a German literary novel: {look}. The central place or scene of this story:"
    for style, look in STYLE_LOOK.items()
}

#: How much of the book text a locally composed image prompt carries.
PROMPT_TEXT_CHARS = 700


def prompt_from_text(text: str, style: str = DEFAULT_STYLE) -> str:
    """An image prompt with no text model: the book's own words behind a register preamble."""
    preamble = _PROMPT_PREAMBLE.get(style, _PROMPT_PREAMBLE[DEFAULT_STYLE])
    return f"{preamble} {' '.join(text.split())[:PROMPT_TEXT_CHARS]}"


def _title_case(family: str) -> str:
    """Uppercase suits the geometric and grotesk display faces; the rest set in title case."""
    return "upper" if FAMILIES[family].casing == "upper" else "title"


def _family_for(template: str, allowed: tuple[str, ...], seed: int) -> str:
    """The template's own face if the mood allows it, else one the mood does."""
    default = TEMPLATE_DEFAULT_FAMILY[template]
    return default if default in allowed else allowed[(seed >> 24) % len(allowed)]


def fallback_direction(
    text: str, title: str, author: str, style: str = DEFAULT_STYLE,
    mood: Mood | None = None,
) -> ArtDirection:
    """Deterministic brief, used when the model is unavailable."""
    seed = seed_from(text, title, author, style, *([mood.key] if mood else []))
    lowered = f"{title} {text}".lower()

    genre = _genre_from_text(lowered)

    if mood:
        tone, candidates = mood.tones, mood.templates
    else:
        # Warm palettes for the illustrated register, the full set otherwise.
        tone = ("charmant", "warm", "heiter") if style == "illustrated" else ()
        candidates = STYLE_TEMPLATES.get(style, TEMPLATES)
    palette = choose_palette(seed, tone)
    template = candidates[(seed >> 8) % len(candidates)]
    family = _family_for(template, mood.type_families, seed) if mood else TEMPLATE_DEFAULT_FAMILY[template]
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
        title_case=_title_case(family),
        style=style,
        lettering=Lettering(location=("top", "bottom")[(seed >> 4) % 2]),
        genre_line=genre,
        image_prompt=prompt_from_text(text, style),
        rationale="Deterministic fallback brief: no art-direction model was reachable.",
    )


async def direct(
    text: str,
    title: str,
    author: str,
    *,
    style: str = DEFAULT_STYLE,
    mood: Mood | None = None,
) -> tuple[ArtDirection, dict]:
    """Produce a cover brief. Returns the brief and metadata about how it was made."""
    from .core import parse

    prompt_text, clipped = clip_prompt(text)
    meta: dict = {"source": "claude", "model": settings.claude_model(), "style": style,
                  "input_clipped": clipped}
    direction = await parse(
        brief_schema(mood), system=system_prompt(style, mood),
        content=user_prompt(title, author, prompt_text), what="art direction",
        max_tokens=8000, meta=meta,
    )
    if direction is None:
        return degrade(meta, meta.pop("failure", "unavailable"), text, title, author, style, mood)
    return direction, meta


def apply_overrides(
    direction: ArtDirection,
    *,
    template: str | None = None,
    type_family: str | None = None,
    palette_key: str | None = None,
    artwork: str | None = None,
    motif: str | None = None,
    genre_line: str | None = None,
    style: str | None = None,
) -> ArtDirection:
    """Let the caller pin any part of the brief.

    Copy the caller pins lands on the brief, not just on the rendered content, so
    the brief stays the single source of truth for what the cover says.
    """
    data = direction.model_dump()
    if template:
        data["template"] = template
        # Each layout is drawn around a particular face; pinning the template
        # without a typeface moves the typeface with it.
        data["type_family"] = type_family or TEMPLATE_DEFAULT_FAMILY.get(
            template, data["type_family"]
        )
    elif type_family:
        data["type_family"] = type_family
    if artwork:
        data["artwork"] = artwork
    if motif:
        data["motif"] = motif
    if genre_line:
        data["genre_line"] = genre_line
    if style:
        data["style"] = style
    if palette_key:
        if palette_key not in PALETTES_BY_KEY:
            raise KeyError(f"unknown palette {palette_key!r}; available: {', '.join(sorted(PALETTES_BY_KEY))}")
        data["house_palette"] = palette_key  # its colours follow (``_house_palette``)
    return ArtDirection.model_validate(data)
