"""Cover concepts per reader type (Prompt B), and the brief made from one.

``write_concepts`` asks Claude for three concepts for one title and reader type --
a main one and two alternatives -- from the Buchkern (``covers.core``), the
reader type's profile (``covers.profiles``) and the constants below. Each concept
names its motif, twist, composition and colour, and picks the layout, type
family and palette the vector typesetting will use.

``to_direction`` turns one concept into the ``ArtDirection`` the renderer takes,
assembling the image prompt in the house's master format -- except that the image
model never sets type: the typography is set afterwards, exactly, as vector
outlines, where the concept's lettering says.
"""

from functools import cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from .artdirection import (
    DRAWN_MOTIFS,
    STYLE_LOOK,
    ArtDirection,
    PaletteKey,
    Style,
    TypeFamily,
    family_description,
    seed_from,
)
from .core import BookCore, parse
from .lettering import Lettering, TitleCase
from .moods import Mood
from .palettes import HEX
from .profiles import PROFILES

#: KONSTANTEN: true of every cover, whatever the type.
CONSTANTS = (
    "any text, letters, words, numbers, pseudo-letters, logos, watermark, book mockup, "
    "border or frame, brand names, real persons, protected artworks"
)

class Concept(BaseModel):
    motif: str = Field(description="Motif, in English, concrete and visible.")
    twist: str = Field(description="Twist (Kniff), in English.")
    composition: str = Field(description="Composition, in English, keeping the lettering's area calm.")
    colour: str = Field(description="Palette, in English: 2-3 named colours.")
    mode: Literal["a", "b", "c"] | None = Field(description="DISKURS only: the mode; otherwise null.")
    template: str
    style: Style = Field(description=ArtDirection.model_fields["style"].description)
    palette: PaletteKey | None = Field(description=ArtDirection.model_fields["house_palette"].description)
    lettering: Lettering = Field(
        description="Where and how the type is set on the image; the picture keeps that area calm."
    )
    type_family: str
    title_case: TitleCase
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
    """``Concepts`` with the mood's layouts, and the type families that suit it named."""
    concept = create_model(
        f"Concept_{mood.key}",
        __base__=Concept,
        template=(Literal[mood.templates], ...),
        type_family=(TypeFamily, Field(description=family_description(mood.type_families))),
    )
    return create_model(
        f"Concepts_{mood.key}",
        __base__=Concepts,
        concepts=(list[concept], Field(description=Concepts.model_fields["concepts"].description)),
    )


def load_concepts(mood: Mood, stored: dict) -> Concepts:
    """Concepts as stored on a cover. Older ones had no register or house palette
    (the mood's register, their own colours), and the oldest only a ``type_zone``."""
    stored = {**stored, "concepts": [
        {"style": mood.style, "palette": None, **c}
        | ({"lettering": {"location": c["type_zone"]}} if "lettering" not in c and "type_zone" in c else {})
        for c in stored.get("concepts", [])
    ]}
    return concepts_schema(mood).model_validate(stored)


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
- Wähle je Konzept das Register (style: illustrated, painterly oder typographic), \
eine Schriftfamilie (type_family) aus allen und eine Hauspalette (palette) oder \
eigene Farben. Profil und Lesetyp empfehlen, sie schränken nicht ein: die Familien, \
die zum Lesetyp passen, sind genannt, und die Farbstrategie des Profils ist ein \
Ausgangspunkt. Nenne 2–3 konkrete Farben und setze Grund-, Schrift-, Akzent- und \
Nebenfarbe als Hex.{extra}
- Leitplanken sind verbindlich, Tabus und Konstanten gelten immer.
- Beschreibe Motiv, Kniff, Komposition und Farbe in sichtbaren, konkreten Begriffen \
auf Englisch: Material, Licht, Tageszeit, Maßstab, Oberfläche.
- Kein Text im Bild: Die Typografie wird danach exakt und direkt auf das Bild gesetzt. \
Plane dafür je Konzept die Beschriftung (lettering): wo der Titel steht (oben, unten, \
links oder rechts als schmale Spalte, diagonal), ob der Name der Autor*in beim Titel \
steht oder getrennt am oberen oder unteren Rand (etwa Titel oben, Autor*in unten), \
Größe, Ausrichtung, bei diagonal den Winkel, und die Farben von Titel und übrigem \
Text -- einfarbig oder als Verlauf aus zwei Farben des Bildes. Die Schrift muss sich \
deutlich vom Bild darunter abheben: plane dort ruhige Flächen und Farben mit starkem \
Kontrast. Die drei Konzepte unterscheiden sich in Position, Register und \
Schriftfamilie; wähle, was zum Motiv passt, nicht immer dasselbe.
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
    content = (
        f"BUCHKERN:\n{_brief(core)}\n\nPROFIL:\n{_profile(mood)}\n\nKONSTANTEN:\n"
        f"Niemals im Bild: {CONSTANTS}."
    )
    written = await parse(
        concepts_schema(mood),
        system=_SYSTEM.format(publisher=publisher, extra=_extra(mood, core)),
        content=content, what="concepts",
    )
    return written if written and written.concepts else None


def image_prompt(core: BookCore, mood: Mood, concept: Concept, concepts: Concepts) -> str:
    """The house master format as one paragraph, without the typography."""
    profile = PROFILES[mood.key]
    style = STYLE_LOOK[concept.style]
    # The reader type's own look, only where it is in the register chosen; it would
    # contradict any other.
    if concept.style == mood.style:
        style += f"; {profile.style}"
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
        f"Composition: {concept.composition}. "
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
        artwork="generated",
        # Drawn only if the image model is unavailable.
        motif=DRAWN_MOTIFS[seed % len(DRAWN_MOTIFS)],  # type: ignore[arg-type]
        ground=concept.ground,
        ink=concept.ink,
        accent=concept.accent,
        secondary=concept.secondary,
        style=concept.style,
        house_palette=concept.palette,
        title_case=concept.title_case,
        lettering=concept.lettering,
        genre_line=core.typography.genre,
        image_prompt=image_prompt(core, mood, concept, concepts),
        rationale=concept.why,
    )
