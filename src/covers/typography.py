"""Typesetting engine: HarfBuzz shaping, outlined glyphs, and display fitting.

Type is shaped with HarfBuzz and emitted as outlined vector paths rather than SVG
``<text>`` elements. That keeps one source of truth for metrics -- the same shaped
advances drive line fitting, the SVG and the PNG -- and it means the
output carries no font dependency, which is what a repro house wants.
"""

import logging
import os
from dataclasses import dataclass
from functools import cache

import uharfbuzz as hb
from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

from . import settings

# fontTools only draws the pens here, but some faces trip warnings in its table
# code that are harmless and drown out real output.
logging.getLogger("fontTools").setLevel(logging.ERROR)

BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_MAC_DIRS = (
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
)


def _find(basename: str) -> str | None:
    dirs = (*_MAC_DIRS, BUNDLED_DIR) if settings.system_fonts() else (BUNDLED_DIR,)
    for d in dirs:
        p = os.path.join(d, basename)
        if os.path.isfile(p):
            return p
    return None


@dataclass(frozen=True)
class FontSpec:
    file: str
    index: int = 0
    #: Variable-font instance, e.g. ``(("wght", 800),)``. Selected at load time,
    #: so the bundled files ship unmodified, as their OFL requires.
    axes: tuple[tuple[str, float], ...] = ()


def _v(file: str, **axes: float) -> FontSpec:
    """A bundled variable face at the given axis values."""
    return FontSpec(file, 0, tuple(axes.items()))


# Bundled faces, all SIL Open Font License (see fonts/OFL-*.txt). The Mac faces
# below are licensed for the machine they ship on, not for a server, so each
# family has an open face of the same register for when they are absent -- or
# switched off with COVERS_SYSTEM_FONTS=0, to see what a deploy will render.
_JOST, _JOST_I = "Jost[wght].ttf", "Jost-Italic[wght].ttf"
_ARCHIVO, _ARCHIVO_I = "Archivo[wdth,wght].ttf", "Archivo-Italic[wdth,wght].ttf"
_NUNITO, _NUNITO_I = (
    "NunitoSans[YTLC,opsz,wdth,wght].ttf",
    "NunitoSans-Italic[YTLC,opsz,wdth,wght].ttf",
)
_PLAYFAIR, _PLAYFAIR_I = "PlayfairDisplay[wght].ttf", "PlayfairDisplay-Italic[wght].ttf"
_BODONI, _BODONI_I = "BodoniModa[opsz,wght].ttf", "BodoniModa-Italic[opsz,wght].ttf"
_BASKERVILLE, _BASKERVILLE_I = "LibreBaskerville[wght].ttf", "LibreBaskerville-Italic[wght].ttf"
_GARAMOND, _GARAMOND_I = "EBGaramond[wght].ttf", "EBGaramond-Italic[wght].ttf"


# Logical families, each weight an ordered list of faces to try. The macOS faces
# come first where they exist: Futura and Optima are German-publishing
# workhorses; Didot and Bodoni cover the neoclassical register that Insel and
# Manesse live in. The last three families are open faces in their own
# right, chosen because German publishing actually sets in them.
def _open(
    upright: str, italic: str, *, display: float = 700, bold: float = 700,
    regular: float = 400, opsz: tuple[float, float, float] | None = None,
) -> dict[str, tuple[FontSpec, ...]]:
    """A bundled variable family: weights per role, and optical sizes if it has them
    (display, bold, text)."""
    size = dict(zip(("display", "bold", "text"), opsz)) if opsz else {}

    def at(file: str, wght: float, role: str) -> tuple[FontSpec, ...]:
        axes = {"wght": wght} | ({"opsz": size[role]} if size else {})
        return (_v(file, **axes),)

    return {
        "display": at(upright, display, "display"),
        "bold": at(upright, bold, "bold"),
        "regular": at(upright, regular, "text"),
        "italic": at(italic, regular, "text"),
    }


def _static(*, display: str, bold: str, regular: str, italic: str) -> dict[str, tuple[FontSpec, ...]]:
    """A bundled family of static files, one per role."""
    return {role: (FontSpec(file),) for role, file in
            (("display", display), ("bold", bold), ("regular", regular), ("italic", italic))}


_GARALDE = {  # EB Garamond -- the Garamond/Sabon of Suhrkamp, Insel and Hanser
    "display": (_v(_GARAMOND, wght=600),),
    "bold": (_v(_GARAMOND, wght=700),),
    "regular": (_v(_GARAMOND, wght=400),),
    "italic": (_v(_GARAMOND_I, wght=400),),
}

FAMILIES: dict[str, dict[str, tuple[FontSpec, ...]]] = {
    "geometric": {  # Futura -- rororo, KiWi, Fischer; open: Jost
        "display": (FontSpec("Futura.ttc", 4), _v(_JOST, wght=800)),  # Condensed ExtraBold
        "bold": (FontSpec("Futura.ttc", 2), _v(_JOST, wght=700)),
        "regular": (FontSpec("Futura.ttc", 0), _v(_JOST, wght=400)),
        "italic": (FontSpec("Futura.ttc", 1), _v(_JOST_I, wght=400)),
    },
    "grotesk": {  # Helvetica Neue; open: Archivo
        "display": (FontSpec("HelveticaNeue.ttc", 9), _v(_ARCHIVO, wght=900, wdth=62)),
        "bold": (FontSpec("HelveticaNeue.ttc", 1), _v(_ARCHIVO, wght=700, wdth=100)),
        "regular": (FontSpec("HelveticaNeue.ttc", 10), _v(_ARCHIVO, wght=400, wdth=100)),
        "italic": (FontSpec("HelveticaNeue.ttc", 2), _v(_ARCHIVO_I, wght=400, wdth=100)),
    },
    "grotesk_condensed": {  # Avenir Next Condensed; open: Nunito Sans at 75% width
        "display": (FontSpec("Avenir Next Condensed.ttc", 8), _v(_NUNITO, wght=900, wdth=75)),
        "bold": (FontSpec("Avenir Next Condensed.ttc", 0), _v(_NUNITO, wght=700, wdth=75)),
        "regular": (FontSpec("Avenir Next Condensed.ttc", 5), _v(_NUNITO, wght=400, wdth=75)),
        "italic": (FontSpec("Avenir Next Condensed.ttc", 4), _v(_NUNITO_I, wght=400, wdth=75)),
    },
    "neoclassical": {  # Didot; open: Playfair Display
        "display": (FontSpec("Didot.ttc", 2), _v(_PLAYFAIR, wght=700)),
        "bold": (FontSpec("Didot.ttc", 2), _v(_PLAYFAIR, wght=700)),
        "regular": (FontSpec("Didot.ttc", 0), _v(_PLAYFAIR, wght=400)),
        "italic": (FontSpec("Didot.ttc", 1), _v(_PLAYFAIR_I, wght=400)),
    },
    # opsz 36, not the 96 maximum: at 96 the hairlines vanish in a cover shown
    # at thumbnail size, which is where most covers are seen.
    "didone": {  # Bodoni 72; open: Bodoni Moda
        "display": (FontSpec("Bodoni 72.ttc", 2), _v(_BODONI, wght=700, opsz=36)),
        "bold": (FontSpec("Bodoni 72.ttc", 2), _v(_BODONI, wght=700, opsz=36)),
        "regular": (FontSpec("Bodoni 72.ttc", 0), _v(_BODONI, wght=400, opsz=11)),
        "italic": (FontSpec("Bodoni 72.ttc", 1), _v(_BODONI_I, wght=400, opsz=11)),
    },
    "literary_serif": {  # Baskerville -- Suhrkamp; open: Libre Baskerville
        "display": (FontSpec("Baskerville.ttc", 1), _v(_BASKERVILLE, wght=700)),
        "bold": (FontSpec("Baskerville.ttc", 1), _v(_BASKERVILLE, wght=700)),
        "regular": (FontSpec("Baskerville.ttc", 0), _v(_BASKERVILLE, wght=400)),
        "italic": (FontSpec("Baskerville.ttc", 2), _v(_BASKERVILLE_I, wght=400)),
    },
    "humanist": {  # Optima -- Hermann Zapf; open: Alegreya Sans
        "display": (FontSpec("Optima.ttc", 4), FontSpec("AlegreyaSans-Black.ttf")),  # ExtraBlack
        "bold": (FontSpec("Optima.ttc", 1), FontSpec("AlegreyaSans-Bold.ttf")),
        "regular": (FontSpec("Optima.ttc", 0), FontSpec("AlegreyaSans-Regular.ttf")),
        "italic": (FontSpec("Optima.ttc", 2), FontSpec("AlegreyaSans-Italic.ttf")),
    },
    "slab": {  # Superclarendon; open: Zilla Slab
        "display": (FontSpec("SuperClarendon.ttc", 7), FontSpec("ZillaSlab-Bold.ttf")),  # Black
        "bold": (FontSpec("SuperClarendon.ttc", 5), FontSpec("ZillaSlab-Bold.ttf")),
        "regular": (FontSpec("SuperClarendon.ttc", 0), FontSpec("ZillaSlab-Regular.ttf")),
        "italic": (FontSpec("SuperClarendon.ttc", 1), FontSpec("ZillaSlab-Italic.ttf")),
    },
    "garalde": _GARALDE,
    # Only the title is blackletter; author and imprint lines, set in tracked
    # capitals, would be unreadable in it, so they stay in Garamond.
    "fraktur": {  # UnifrakturMaguntia titles, for Märchen and the historical
        **_GARALDE,
        "display": (FontSpec("UnifrakturMaguntia-Book.ttf"),),
        "bold": (_v(_GARAMOND, wght=600),),
    },
    # --- More open faces, all bundled, all with full German coverage ---------
    "cormorant": _open("CormorantGaramond[wght].ttf", "CormorantGaramond-Italic[wght].ttf",
                       display=700, bold=700, regular=500),  # an elegant display Garamond
    "crimson": _open("CrimsonPro[wght].ttf", "CrimsonPro-Italic[wght].ttf"),  # Minion-like book face
    "lora": _open("Lora[wght].ttf", "Lora-Italic[wght].ttf"),  # a warm contemporary serif
    "source_serif": _open("SourceSerif4[opsz,wght].ttf", "SourceSerif4-Italic[opsz,wght].ttf",
                          opsz=(60, 20, 12)),  # sober transitional, for non-fiction
    "spectral": _static(display="Spectral-ExtraBold.ttf", bold="Spectral-Bold.ttf",
                        regular="Spectral-Regular.ttf", italic="Spectral-Italic.ttf"),
    "fraunces": _open("Fraunces[SOFT,WONK,opsz,wght].ttf", "Fraunces-Italic[SOFT,WONK,opsz,wght].ttf",
                      opsz=(144, 36, 14)),  # soft, wonky Old Style; very current
    "dm_serif": _static(display="DMSerifDisplay-Regular.ttf", bold="DMSerifDisplay-Regular.ttf",
                        regular="DMSerifDisplay-Regular.ttf", italic="DMSerifDisplay-Italic.ttf"),
    "caslon": {  # Libre Caslon: the display cut for titles, the text cut beneath
        "display": (FontSpec("LibreCaslonDisplay-Regular.ttf"),),
        "bold": (_v("LibreCaslonText[wght].ttf", wght=700),),
        "regular": (_v("LibreCaslonText[wght].ttf", wght=400),),
        "italic": (_v("LibreCaslonText-Italic[wght].ttf", wght=400),),
    },
    "alegreya": _open("Alegreya[wght].ttf", "Alegreya-Italic[wght].ttf", display=800),
    "newsreader": _open("Newsreader[opsz,wght].ttf", "Newsreader-Italic[opsz,wght].ttf",
                        opsz=(72, 20, 12)),  # a newspaper serif, for reportage and essays
    "young_serif": _static(display="YoungSerif-Regular.ttf", bold="YoungSerif-Regular.ttf",
                           regular="YoungSerif-Regular.ttf", italic="YoungSerif-Regular.ttf"),
    "gloock": _static(display="Gloock-Regular.ttf", bold="Gloock-Regular.ttf",
                      regular="Gloock-Regular.ttf", italic="Gloock-Regular.ttf"),
    # Abril Fatface is for titles only; the lines beneath are set in Lora.
    "abril": {**_open("Lora[wght].ttf", "Lora-Italic[wght].ttf"),
              "display": (FontSpec("AbrilFatface-Regular.ttf"),)},
    "montserrat": _open("Montserrat[wght].ttf", "Montserrat-Italic[wght].ttf", display=800),
    "josefin": _open("JosefinSans[wght].ttf", "JosefinSans-Italic[wght].ttf"),  # art-deco geometric
    "work_sans": _open("WorkSans[wght].ttf", "WorkSans-Italic[wght].ttf", display=800),
    "inter": _open("Inter[opsz,wght].ttf", "Inter-Italic[opsz,wght].ttf", display=800,
                   opsz=(32, 14, 14)),
    "space_grotesk": _open("SpaceGrotesk[wght].ttf", "SpaceGrotesk[wght].ttf"),  # no italic cut
    "syne": _open("Syne[wght].ttf", "Syne[wght].ttf", display=800),  # no italic cut
    "oswald": _open("Oswald[wght].ttf", "Oswald[wght].ttf", bold=600),  # condensed; no italic
    # Bebas Neue has capitals only and one weight; the smaller lines are in Oswald.
    "bebas": {**_open("Oswald[wght].ttf", "Oswald[wght].ttf", bold=500),
              "display": (FontSpec("BebasNeue-Regular.ttf"),)},
    "barlow_condensed": _static(display="BarlowCondensed-Black.ttf", bold="BarlowCondensed-Bold.ttf",
                                regular="BarlowCondensed-Regular.ttf", italic="BarlowCondensed-Italic.ttf"),
    "grenze_gotisch": _open("GrenzeGotisch[wght].ttf", "GrenzeGotisch[wght].ttf", bold=600),
    "cinzel": _open("Cinzel[wght].ttf", "Cinzel[wght].ttf", bold=600),  # Roman capitals only
    "meta": {  # Fira Sans -- Erik Spiekermann's open successor to FF Meta
        "display": (FontSpec("FiraSans-Black.ttf"),),
        "bold": (FontSpec("FiraSans-Bold.ttf"),),
        "regular": (FontSpec("FiraSans-Regular.ttf"),),
        "italic": (FontSpec("FiraSans-Italic.ttf"),),
    },
}

TYPE_FAMILIES = tuple(FAMILIES)


class MissingFontError(RuntimeError):
    pass


class Face:
    """One face at one instance, all through HarfBuzz: shaping, metrics, outlines.

    One engine for all three means advances and outlines can never disagree about
    a variable font's instance.
    """

    def __init__(self, hb_face: hb.Face, axes: tuple[tuple[str, float], ...] = ()) -> None:
        self._hb_font = hb.Font(hb_face)
        if axes:
            self._hb_font.set_variations(dict(axes))
        self.upem: int = hb_face.upem
        self.cap_height = (
            self._hb_font.get_metric_position_with_fallback(hb.OTMetricsTag.CAP_HEIGHT)
            / self.upem
        )

    def shape(self, text: str) -> list[tuple[int, float, float, float]]:
        """Shape ``text`` into ``(glyph_id, x_advance, x_offset, y_offset)`` in em units."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self._hb_font, buf)
        return [
            (info.codepoint, pos.x_advance / self.upem, pos.x_offset / self.upem,
             pos.y_offset / self.upem)
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
        ]

    def measure(self, text: str, tracking: float = 0.0) -> float:
        """Advance width of ``text`` in em units, including letterspacing."""
        glyphs = self.shape(text)
        if not glyphs:
            return 0.0
        total = sum(g[1] for g in glyphs) + tracking * (len(glyphs) - 1)
        return max(0.0, total)

    def run(
        self,
        text: str,
        size: float,
        *,
        tracking: float = 0.0,
        fill: str = "#000000",
        x: float = 0.0,
        y: float = 0.0,
    ) -> str:
        """Outline ``text`` as a single SVG path with its baseline on ``y``."""
        pen = SVGPathPen(None)
        # Outlines come back in font units, so the scale carries the 1/upem.
        unit = size / self.upem
        cursor = 0.0
        for gid, adv, xo, yo in self.shape(text):
            t = Transform().translate(
                x + (cursor + xo) * size, y - yo * size
            ).scale(unit, -unit)
            self._hb_font.draw_glyph_with_pen(gid, TransformPen(pen, t))
            cursor += adv + tracking
        d = pen.getCommands()
        return f'<path d="{d}" fill="{fill}"/>' if d else ""


@cache
def _hb_face(path: str, index: int) -> hb.Face:
    """Parsed once per file, however many weights are instanced from it."""
    return hb.Face(hb.Blob.from_file_path(path), index)


@cache
def face(family: str, weight: str = "display") -> Face:
    """The first of the family's faces for ``weight`` that is present."""
    for spec in FAMILIES[family][weight]:
        if path := _find(spec.file):
            return Face(_hb_face(path, spec.index), spec.axes)
    raise MissingFontError(
        f"no face for {family}/{weight}; the bundled fonts in {BUNDLED_DIR} are missing"
    )


def check_fonts() -> None:
    """Load every family and weight, so a missing face fails at startup, not mid-render."""
    for family, weights in FAMILIES.items():
        for weight in weights:
            face(family, weight)


# --------------------------------------------------------------------------- #
# Line breaking
# --------------------------------------------------------------------------- #


_UMLAUT_CAPS = frozenset("ÄÖÜ")


def umlaut_leading(lines: list[str], base: float) -> float:
    """Open the leading when a line carries a capital umlaut.

    Tight display leading collides with the diaeresis on Ä/Ö/Ü, which German
    titles are full of, so any line after the first that carries one buys the
    whole block a little more air.
    """
    if any(_UMLAUT_CAPS & set(line) for line in lines[1:]):
        return base + 0.12
    return base


@dataclass
class TextBlock:
    """A fitted run of display type."""

    lines: list[str]
    size: float
    leading: float
    tracking: float
    widths: list[float]

    @property
    def height(self) -> float:
        """First baseline to last; add the face's cap height for the visual height."""
        return (len(self.lines) - 1) * self.leading * self.size

    @property
    def max_width(self) -> float:
        return max(self.widths) if self.widths else 0.0


def _partition(
    face_: Face, words: list[str], n_lines: int, tracking: float
) -> tuple[float, list[str]]:
    """Split ``words`` into ``n_lines`` minimising the widest line, then ragging.

    Word order is preserved, so this is a linear partition; exhaustive DP is
    cheap at title lengths. Returns the widest line's width in em, and the lines.
    """
    n_words = len(words)
    n_lines = max(1, min(n_lines, n_words))

    @cache
    def measure_span(i: int, j: int) -> float:
        return face_.measure(" ".join(words[i:j]), tracking)

    @cache
    def go(i: int, j: int) -> tuple[float, float, tuple[int, ...]]:
        if j == 1:
            w = measure_span(i, n_words)
            return (w, 0.0, (n_words,))
        best: tuple[float, float, tuple[int, ...]] | None = None
        # leave at least one word for each remaining line
        for k in range(i + 1, n_words - j + 2):
            w = measure_span(i, k)
            sub_max, sub_sq, splits = go(k, j - 1)
            cand_max = max(w, sub_max)
            cand = (cand_max, sub_sq + w * w, (k,) + splits)
            if best is None or (round(cand[0], 6), cand[1]) < (
                round(best[0], 6),
                best[1],
            ):
                best = cand
        assert best is not None
        return best

    max_w, _, splits = go(0, n_lines)
    lines, start = [], 0
    for end in splits:
        lines.append(" ".join(words[start:end]))
        start = end
    return max_w, lines


def fit_display(
    text: str,
    family: str,
    *,
    max_width: float,
    max_height: float,
    max_lines: int = 4,
    leading: float = 0.88,
    tracking: float = -0.02,
) -> TextBlock:
    """Set ``text`` as large as will fit the measure, choosing the line count.

    Tries every line count up to ``max_lines`` and keeps the one that yields the
    largest type; ties go to the fewest lines. Display type gets slightly negative
    tracking, which is what the size asks for optically.
    """
    words = [w for w in text.split() if w]
    if not words:
        return TextBlock([], 0.0, leading, tracking, [])
    f = face(family, "display")

    best: TextBlock | None = None
    for n in range(1, min(max_lines, len(words)) + 1):
        max_w_em, lines = _partition(f, words, n, tracking)
        if max_w_em <= 0:
            continue
        lead = umlaut_leading(lines, leading)
        by_width = max_width / max_w_em
        # cap line down to the last baseline
        h_em = (n - 1) * lead + f.cap_height
        by_height = max_height / h_em if h_em > 0 else float("inf")
        size = min(by_width, by_height)
        if best is None or size > best.size + 1e-9:
            widths = [f.measure(ln, tracking) * size for ln in lines]
            best = TextBlock(lines, size, lead, tracking, widths)
    assert best is not None
    return best
