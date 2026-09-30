"""Moods: who a cover is for decides how it feels and what it may be set in.

A mood fixes the register, steers the palette, and narrows the layouts and type
families the brief may choose from. Every mood uses ``picture``, the layout that
sets the type straight onto a full-bleed picture: no panels, bands or plates. The art director still decides per book --
but inside these limits, so a thriller cannot come back in Garamond on pastel.

Pure data, so the schema can name the moods without importing the directors.
"""

from dataclasses import dataclass
from typing import Literal, get_args

MoodKey = Literal["heart", "suspense", "trend", "discourse"]


@dataclass(frozen=True)
class Mood:
    key: str
    #: The register the brief is written in (see ``artdirection.STYLE_GUIDANCE``).
    style: str
    #: What the reader is looking for, told to the art director.
    guidance: str
    templates: tuple[str, ...]
    type_families: tuple[str, ...]
    #: Palette tone words (see ``palettes.Palette.tone``) for the fallback brief.
    tones: tuple[str, ...]


MOODS: dict[str, Mood] = {
    m.key: m
    for m in (
        Mood(
            "heart",
            style="illustrated",
            guidance=(
                "Readers looking for feeling: love, family, friendship, the feel-good "
                "and the bittersweet. Warm, tender and inviting; people and small "
                "intimate scenes, soft light, hand-mixed colour. Never cold, never "
                "menacing."
            ),
            templates=("picture",),
            type_families=("humanist", "literary_serif", "garalde", "neoclassical"),
            tones=("warm", "charmant", "romantisch", "zart", "intim", "heiter"),
        ),
        Mood(
            "suspense",
            style="painterly",
            guidance=(
                "Readers looking for tension: crime, thriller, the psychological and "
                "the uncanny. Dark, atmospheric, high contrast; night, fog, a single "
                "figure or a withheld detail. The cover should promise a threat, not "
                "explain it."
            ),
            templates=("picture",),
            type_families=("grotesk", "grotesk_condensed", "geometric", "slab"),
            tones=("dunkel", "spannung", "krimi", "nächtlich", "hart", "kalt"),
        ),
        Mood(
            "trend",
            style="illustrated",
            guidance=(
                "Readers who find books through BookTok and the bestseller table: "
                "romantasy, young adult, the book everyone is reading. Bold, "
                "saturated and graphic; it has to stop the scroll at thumbnail size "
                "and look like it belongs to now."
            ),
            templates=("picture",),
            type_families=("meta", "geometric", "grotesk_condensed", "neoclassical"),
            tones=("pop", "jung", "laut", "grell", "frisch", "traumhaft"),
        ),
        Mood(
            "discourse",
            style="typographic",
            guidance=(
                "Readers looking for ideas: prize-list literary fiction, essays, "
                "politics, history, non-fiction. Restrained and confident; typography "
                "carries the cover, colour is flat and few, imagery abstract if any. "
                "Serious without being dull."
            ),
            templates=("picture",),
            type_families=("garalde", "literary_serif", "didone", "grotesk", "meta"),
            tones=("sachlich", "ernst", "literarisch", "klar", "essay", "politisch"),
        ),
    )
}

assert set(MOODS) == set(get_args(MoodKey)), "MoodKey and MOODS name different moods"
