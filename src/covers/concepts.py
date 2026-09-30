"""Cover concepts per reader type (Prompt B), and the brief made from one.

``write_concepts`` asks Claude for three concepts for one title and reader type --
a main one and two alternatives -- from the Buchkern (``covers.core``), the
reader type's profile (``covers.profiles``) and the constants below. Each concept
names its motif, twist, composition and colour, and picks the layout, type
family and palette the vector typesetting will use.

``to_direction`` turns one concept into the ``ArtDirection`` the renderer takes,
assembling the image prompt in the house's master format -- except that the image
model never sets type: the typography is set afterwards, exactly, as vector
outlines. The prompt tells it where the type will go instead.
"""

import logging
from functools import cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from . import settings
from .artdirection import DRAWN_MOTIFS, HEX, ArtDirection, seed_from
from .core import BookCore, client
from .moods import Mood
from .profiles import PROFILES
from .typography import describe

log = logging.getLogger("covers.concepts")

#: KONSTANTEN: true of every cover, whatever the type.
CONSTANTS = (
    "any text, letters, words, numbers, pseudo-letters, logos, watermark, book mockup, "
    "border or frame, brand names, real persons, protected artworks"
)

#: Where each layout sets the type, so the picture leaves that zone calm.
TYPE_ZONES = {
    "illustrated_full": (
        "keep the subject in the upper two thirds; the lower third will be covered by the type panel"
    ),
    "photo_duotone": (
        "the image fills the upper part of the cover, above the type; let the subject fill the frame"
    ),
    "kiwi_flat": (
        "the image fills the lower half of the cover, below the type; let the subject fill the frame"
    ),
    "rororo_band": (
        "a solid colour band will cross the middle; keep the subject above or below the middle"
    ),
    "didone_centre": (
        "a centred vignette framed by type above and below; one compact subject"
    ),
    "type_block": "no image; the type fills the cover",
}

#: ``picture`` sets the type straight onto the image, in the zone the concept picks.
_PICTURE_ZONE = (
    "the author, title and genre will be set directly onto the picture across the {third} "
    "third, with nothing behind them: keep that area calm and even -- open sky, water, "
    "mist, a plain wall, soft shadow -- with no objects, faces or busy detail in it, and "
    "the subject in the rest of the frame; a small publisher line sits at the bottom edge"
)


def zone_text(template: str, type_zone: str = "top") -> str:
    """What the image prompt says about where the type will go."""
    if template == "picture":
        return _PICTURE_ZONE.format(third="upper" if type_zone == "top" else "lower")
    return TYPE_ZONES[template]


class Concept(BaseModel):
    motif: str = Field(description="Motif, in English, concrete and visible.")
    twist: str = Field(description="Twist (Kniff), in English.")
    composition: str = Field(description="Composition, in English, keeping the layout's type zone calm.")
    colour: str = Field(description="Palette, in English: 2-3 named colours.")
    mode: Literal["a", "b", "c"] | None = Field(description="DISKURS only: the mode; otherwise null.")
    template: str
    type_zone: Literal["top", "bottom"] = Field(
        description="picture: where the type sits on the image -- the calm third it keeps."
    )
    type_family: str
    title_case: Literal["upper", "title", "as_is"]
    ground: str = Field(pattern=HEX, description="Ground colour of the cover, hex.")
    ink: str = Field(pattern=HEX, description="Type colour, legible on the ground, hex.")
    accent: str = Field(pattern=HEX, description="Accent for rules and the genre line, hex.")
    secondary: str = Field(pattern=HEX, description="Supporting colour, hex.")
    why: str = Field(description="Deutsch, eine Zeile: Motiv · Kniff · Farbe · warum das zum Lesetyp passt.")


class Concepts(BaseModel):
    concepts: list[Concept] = Field(description="Exactly three: the main concept first, then two alternatives.")
    respect: str = Field(description="The guardrails, in English; 'none' if there are none.")
    avoid: str = Field(description="The taboos, in English; 'none' if there are none.")


@cache
def concepts_schema(mood: Mood) -> type[Concepts]:
    """``Concepts`` with each concept's layout and face narrowed to the mood."""
    concept = create_model(
        f"Concept_{mood.key}",
        __base__=Concept,
        template=(Literal[mood.templates], ...),
        type_family=(Literal[mood.type_families], Field(description=describe(mood.type_families))),
    )
    return create_model(
        f"Concepts_{mood.key}",
        __base__=Concepts,
        concepts=(list[concept], Field(description=Concepts.model_fields["concepts"].description)),
    )


_SYSTEM = """\
Du bist Art Director bei {publisher} und entwirfst Coverkonzepte für einen \
Bildgenerator. Du erhältst: (1) den BUCHKERN eines Titels, (2) das PROFIL eines \
Lesetyps, (3) die KONSTANTEN.

Schreibe DREI Konzepte für diesen Lesetyp: einen Hauptvorschlag (das erste) und zwei \
Alternativen.
- Jedes Konzept nutzt ein ANDERES Motiv aus den zentralen Motiven (oder eine klar \
andere Sicht darauf), ausgewählt nach der Motivlogik des Profils.
- Jedes hat einen eigenen Kniff, abgeleitet aus den Kniff-Ansätzen und dem typischen \
Kniff des Profils.
- Wähle die Palette aus der Farbstrategie des Profils passend zum Stoff; nenne 2–3 \
konkrete Farben und setze Grund-, Schrift-, Akzent- und Nebenfarbe als Hex. Die \
Schriftfarbe muss auf dem Grund deutlich lesbar sein.{extra}
- Leitplanken sind verbindlich, Tabus und Konstanten gelten immer.
- Beschreibe Motiv, Kniff, Komposition und Farbe in sichtbaren, konkreten Begriffen \
auf Englisch: Material, Licht, Tageszeit, Maßstab, Oberfläche.
- Kein Text im Bild: Die Typografie wird danach exakt gesetzt. Wähle dafür je Konzept \
das Layout (template) und die Schriftfamilie (type_family) und plane die Komposition \
so, dass die Typo-Zone des Layouts ruhig bleibt:
{zones}
- respect: die Leitplanken, übersetzt ins Englische; avoid: die Tabus, übersetzt."""


def _brief(core: BookCore) -> str:
    t = core.typography
    return "\n".join((
        f"Ort und Zeit: {core.place_and_time}",
        f"Stoff: {core.story}",
        f"Emotionaler Kern: {core.emotional_core}",
        f"Zentrale Motive: {'; '.join(core.motifs)}",
        f"Kniff-Ansätze: {'; '.join(core.twists)}",
        f"Tonalität: {', '.join(core.tone)}",
        f"Leitplanken: {core.guardrails}",
        f"Tabus: {core.taboos}",
        f"Wiedererkennung: {core.recognition}",
        f"Genre-Indiz: {core.genre_hint}",
        f"Typo-Daten: {t.author} · {t.title} · {t.subtitle or 'keiner'} · {t.genre} · {t.publisher_line}",
    ))


def _profile(mood: Mood) -> str:
    p = PROFILES[mood.key]
    lines = [
        f"Lesetyp: {p.label}",
        f"Motivlogik: {p.motif_logic}",
        f"Typischer Kniff: {p.twist}",
        f"Farbstrategie: {p.colour_strategy}",
        f"Komposition: {p.composition}",
        f"Stil: {p.style}",
    ]
    lines += [f"Modus {k}: {v}" for k, v in p.modes.items()]
    lines += [f"Ausprägung {k}: {v}" for k, v in p.registers.items()]
    return "\n".join(lines)


def _extra(mood: Mood, core: BookCore) -> str:
    profile = PROFILES[mood.key]
    if profile.modes:
        return "\n- Wähle pro Konzept einen Modus (a/b/c) und nutze möglichst verschiedene."
    if profile.registers and core.suspense_register:
        return f"\n- Nutze die Ausprägung „{core.suspense_register}“."
    return ""


async def write_concepts(core: BookCore, mood: Mood, *, publisher: str) -> Concepts | None:
    """Three concepts for this title and reader type, or None if Claude is unavailable."""
    import anthropic

    if (claude := client()) is None:
        return None
    zones = "\n".join(
        f"  {t}: " + (zone_text(t, "top") + " (type_zone top; bottom: the same in the lower third)"
                      if t == "picture" else TYPE_ZONES[t])
        for t in mood.templates
    )
    content = (
        f"BUCHKERN:\n{_brief(core)}\n\nPROFIL:\n{_profile(mood)}\n\nKONSTANTEN:\n"
        f"Niemals im Bild: {CONSTANTS}."
    )
    try:
        response = await claude.messages.parse(
            model=settings.claude_model(),
            max_tokens=16000,
            system=_SYSTEM.format(publisher=publisher, extra=_extra(mood, core), zones=zones),
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": content}],
            output_format=concepts_schema(mood),
        )
    except (anthropic.APIError, TypeError, ValueError) as exc:
        log.warning("concepts failed (%s)", exc)
        return None
    if response.stop_reason == "refusal" or not response.parsed_output.concepts:
        return None
    return response.parsed_output


def image_prompt(core: BookCore, mood: Mood, concept: Concept, concepts: Concepts) -> str:
    """The house master format as one paragraph, without the typography."""
    profile = PROFILES[mood.key]
    style = profile.style
    if concept.mode and concept.mode in profile.modes:
        style += f"; {profile.modes[concept.mode]}"
    elif profile.registers and core.suspense_register in profile.registers:
        style += f"; {profile.registers[core.suspense_register]}"
    avoid = ", ".join(x for x in (profile.avoid, concepts.avoid, CONSTANTS) if x and x != "none")
    respect = f" Respect: {concepts.respect}." if concepts.respect not in ("", "none") else ""
    return (
        "Book cover artwork, print-ready, with no text of any kind: the typography is "
        f"set separately. Story: {core.place_and_time}, the mood is {', '.join(core.tone)}. "
        f"Motif: {concept.motif}. Twist: {concept.twist}. "
        f"Composition: {concept.composition}; {zone_text(concept.template, concept.type_zone)}. "
        f"Colour: {concept.colour}. Style: {style}.{respect} Avoid: {avoid}."
    )


def to_direction(core: BookCore, mood: Mood, concepts: Concepts, index: int = 0) -> ArtDirection:
    """The renderer's brief for one of the concepts."""
    concept = concepts.concepts[index]
    seed = seed_from(core.typography.title, concept.motif)
    return ArtDirection(
        mood=", ".join(core.tone),
        keywords=list(core.motifs),
        template=concept.template,  # type: ignore[arg-type]
        type_family=concept.type_family,  # type: ignore[arg-type]
        # type_block has no room for a picture; the others paint the concept.
        artwork="none" if concept.template == "type_block" else "generated",
        # Drawn only if the image model is unavailable.
        motif=DRAWN_MOTIFS[seed % len(DRAWN_MOTIFS)],  # type: ignore[arg-type]
        ground=concept.ground,
        ink=concept.ink,
        accent=concept.accent,
        secondary=concept.secondary,
        title_case=concept.title_case,
        type_zone=concept.type_zone,
        genre_line=core.typography.genre,
        image_prompt=image_prompt(core, mood, concept, concepts),
        rationale=concept.why,
    )
