"""Flat-colour palettes in the register German literary imprints print in.

Each palette is a four-colour system: a ground the whole cover sits on, an ink
that has to stay legible on it, an accent for rules, bands and the genre line,
and a secondary for motifs. They double as the fallback vocabulary
when no art-direction model is reachable.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    key: str
    label: str
    ground: str
    ink: str
    accent: str
    secondary: str
    #: Rough register, used to match a palette to a mood without a model call.
    tone: tuple[str, ...] = ()


def rgb(hex_colour: str) -> tuple[int, int, int]:
    r, g, b = bytes.fromhex(hex_colour.lstrip("#"))
    return r, g, b


def luminance(hex_colour: str) -> float:
    r, g, b = (c / 255 for c in rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def darkest_and_lightest(hexes: tuple[str, ...]) -> tuple[str, str]:
    """The two ends of a palette, for mapping artwork onto it."""
    ordered = sorted(hexes, key=luminance)
    return ordered[0], ordered[-1]


def contrasting_ink(ground: str, ink: str, alt: str) -> str:
    """Pick whichever of ``ink``/``alt`` reads better on ``ground``."""
    lg = luminance(ground)
    return ink if abs(luminance(ink) - lg) >= abs(luminance(alt) - lg) else alt


PALETTES: tuple[Palette, ...] = (
    Palette("rororo_rot", "rororo Rot", "#E8412A", "#FFFFFF", "#12100E", "#F6E7C6",
            ("laut", "klassisch", "dringlich", "modern")),
    Palette("kobalt", "Kobalt", "#1B3A8C", "#F4F1E8", "#E8B33C", "#0E1E4A",
            ("kühl", "ernst", "politisch", "urban")),
    Palette("schwefel", "Schwefel", "#F2D53C", "#16130E", "#C0392B", "#8A7A1E",
            ("grell", "ironisch", "pop", "wach")),
    Palette("pergament", "Pergament", "#E9E2D0", "#1A1A18", "#8C2B20", "#A9A090",
            ("literarisch", "ruhig", "historisch", "klassisch")),
    Palette("nachtblau", "Nachtblau", "#101A2C", "#EDE7DA", "#C9A44C", "#26364F",
            ("dunkel", "melancholisch", "nächtlich", "spannung")),
    Palette("moos", "Moos", "#2E4034", "#F0EBDD", "#D9743F", "#55684C",
            ("erdig", "ländlich", "natur", "schwer")),
    Palette("beton", "Beton", "#D5D2CB", "#1C1C1A", "#2F5BE8", "#8E8B83",
            ("nüchtern", "urban", "sachlich", "kalt")),
    Palette("bordeaux", "Bordeaux", "#5C1A26", "#F2E6DA", "#D9A94C", "#8A3140",
            ("schwer", "tragisch", "opulent", "historisch")),
    Palette("signalorange", "Signalorange", "#F26722", "#17120E", "#FFFFFF", "#A63F10",
            ("laut", "pop", "jung", "dringlich")),
    Palette("eisblau", "Eisblau", "#CFE0E8", "#12252E", "#E24A33", "#7FA3B3",
            ("kühl", "klar", "winter", "distanziert")),
    Palette("graphit", "Graphit", "#211F1E", "#EDEAE3", "#E4572E", "#4A4644",
            ("dunkel", "hart", "krimi", "modern")),
    Palette("rosenholz", "Rosenholz", "#E6C9BE", "#2A1A18", "#6B3A2E", "#B98C7C",
            ("zart", "intim", "familie", "warm")),
    Palette("kiwi_weiss", "KiWi Weiß", "#F7F5F0", "#14120F", "#E2231A", "#B4AFA4",
            ("klar", "sachlich", "essay", "modern")),
    Palette("türkis", "Türkis", "#0E6B6B", "#F2EFE4", "#F0C33C", "#0A4A4A",
            ("frisch", "fremd", "reise", "sommer")),
    Palette("violett", "Violett", "#3B2455", "#EFE8F2", "#E8B33C", "#5D3E7A",
            ("traumhaft", "phantastisch", "nacht", "rausch")),
    Palette("sand", "Sand", "#D8C39A", "#241E15", "#1F4E5F", "#A8916A",
            ("trocken", "süden", "erinnerung", "warm")),
    # Warmer register, for illustrated covers that want charm rather than rigour.
    Palette("honig", "Honig", "#F6E3B6", "#3A2415", "#D2662A", "#8FA65B",
            ("warm", "charmant", "sommer", "heiter")),
    Palette("terrakotta", "Terrakotta", "#E8A87C", "#3B2118", "#7C4A32", "#4E7C6A",
            ("warm", "charmant", "südlich", "erdig")),
    Palette("pistazie", "Pistazie", "#CFE0B4", "#2A331E", "#C4553A", "#7B9A5E",
            ("frisch", "heiter", "ländlich", "charmant")),
    Palette("himmelblau", "Himmelblau", "#BBD9EC", "#1E3446", "#E0714A", "#E8C86A",
            ("leicht", "heiter", "kindheit", "sommer")),
    Palette("rosenrot", "Rosenrot", "#F2C9C4", "#3A1C22", "#B33A46", "#6E8F7B",
            ("zart", "charmant", "romantisch", "warm")),
    Palette("puder", "Puder", "#F0E0D0", "#33241C", "#C2703F", "#89A8A0",
            ("sanft", "nostalgisch", "charmant", "intim")),
    Palette("lavendel", "Lavendel", "#D8CCE6", "#2C2338", "#C96F8A", "#8AA86E",
            ("traumhaft", "zart", "heiter", "phantastisch")),
)

PALETTES_BY_KEY = {p.key: p for p in PALETTES}
PALETTE_KEYS = tuple(p.key for p in PALETTES)


def choose_palette(seed: int, keywords: tuple[str, ...] = ()) -> Palette:
    """Deterministically pick a palette, preferring a tone keyword match."""
    if keywords:
        wanted = {k.lower() for k in keywords}
        matches = [p for p in PALETTES if wanted & set(p.tone)]
        if matches:
            return matches[seed % len(matches)]
    return PALETTES[seed % len(PALETTES)]
