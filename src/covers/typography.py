"""Typesetting engine: HarfBuzz shaping, outlined glyphs, and display fitting.

Type is shaped with HarfBuzz and emitted as outlined vector paths rather than SVG
``<text>`` elements. That keeps one source of truth for metrics -- the same shaped
advances drive line fitting, the SVG and the PNG -- and it means the
output carries no font dependency, which is what a repro house wants.
"""

import logging
import os
from dataclasses import dataclass
from functools import cache, lru_cache

import uharfbuzz as hb
from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# Several of the macOS system faces have minor table quirks that fontTools warns
# about on every load. They are harmless and the warnings drown out real output.
logging.getLogger("fontTools").setLevel(logging.ERROR)

_FONT_DIRS = (
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts/truetype",
    os.path.join(os.path.dirname(__file__), "fonts"),
)


def _find(basename: str) -> str | None:
    for d in _FONT_DIRS:
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
# family falls back to an open face of the same register when they are absent.
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
# come first where they exist: Futura, Palatino and Optima are the German-
# publishing workhorses; Didot and Bodoni cover the neoclassical register that
# Insel and Manesse live in. The last three families are open faces in their own
# right, chosen because German publishing actually sets in them.
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
    "garalde": {  # EB Garamond -- the Garamond/Sabon of Suhrkamp, Insel and Hanser
        "display": (_v(_GARAMOND, wght=600),),
        "bold": (_v(_GARAMOND, wght=700),),
        "regular": (_v(_GARAMOND, wght=400),),
        "italic": (_v(_GARAMOND_I, wght=400),),
    },
    # Only the title is blackletter; author and imprint lines, set in tracked
    # capitals, would be unreadable in it, so they fall to Garamond.
    "fraktur": {  # UnifrakturMaguntia titles over Garamond, for Märchen and the historical
        "display": (FontSpec("UnifrakturMaguntia-Book.ttf"),),
        "bold": (_v(_GARAMOND, wght=600),),
        "regular": (_v(_GARAMOND, wght=400),),
        "italic": (_v(_GARAMOND_I, wght=400),),
    },
    "meta": {  # Fira Sans -- Erik Spiekermann's open successor to FF Meta
        "display": (FontSpec("FiraSans-Black.ttf"),),
        "bold": (FontSpec("FiraSans-Bold.ttf"),),
        "regular": (FontSpec("FiraSans-Regular.ttf"),),
        "italic": (FontSpec("FiraSans-Italic.ttf"),),
    },
}

#: Tried in order when a requested family has no usable face on this machine.
FAMILY_FALLBACK = (
    "geometric",
    "grotesk",
    "grotesk_condensed",
    "humanist",
    "literary_serif",
    "garalde",
    "neoclassical",
    "didone",
    "slab",
    "meta",
    "fraktur",
)

TYPE_FAMILIES = tuple(FAMILIES)


class MissingFontError(RuntimeError):
    pass


class Face:
    def __init__(self, path: str, index: int, axes: tuple[tuple[str, float], ...] = ()) -> None:
        blob = hb.Blob.from_file_path(path)
        self._hb_face = hb.Face(blob, index)
        self._hb_font = hb.Font(self._hb_face)
        self.upem: int = self._hb_face.upem
        self._tt = TTFont(path, fontNumber=index, lazy=True)
        # Shaping (advances) and outlines must agree on the instance.
        location = dict(axes) or None
        if location:
            self._hb_font.set_variations(location)
        self._glyphset = self._tt.getGlyphSet(location=location)
        self._order = self._tt.getGlyphOrder()
        os2 = self._tt["OS/2"] if "OS/2" in self._tt else None
        cap = getattr(os2, "sCapHeight", 0) or 0
        self.cap_height = (cap / self.upem) if cap else self._measured_cap_height()

    def _measured_cap_height(self) -> float:
        try:
            glyf = self._tt["glyf"]
            g = glyf["H"]
            return (g.yMax or 0) / self.upem
        except Exception:
            return 0.7

    def shape(self, text: str) -> list[tuple[str, float, float, float]]:
        """Shape ``text`` into ``(glyph_name, x_advance, x_offset, y_offset)`` in em units."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self._hb_font, buf)
        out = []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append(
                (
                    self._order[info.codepoint],
                    pos.x_advance / self.upem,
                    pos.x_offset / self.upem,
                    pos.y_offset / self.upem,
                )
            )
        return out

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
        pen = SVGPathPen(self._glyphset)
        # Outlines come back in font units, so the scale carries the 1/upem.
        unit = size / self.upem
        cursor = 0.0
        for name, adv, xo, yo in self.shape(text):
            t = Transform().translate(
                x + (cursor + xo) * size, y - yo * size
            ).scale(unit, -unit)
            self._glyphset[name].draw(TransformPen(pen, t))
            cursor += adv + tracking
        d = pen.getCommands()
        return f'<path d="{d}" fill="{fill}"/>' if d else ""


@lru_cache(maxsize=64)
def _face(path: str, index: int, axes: tuple[tuple[str, float], ...]) -> Face:
    return Face(path, index, axes)


@lru_cache(maxsize=256)
def face(family: str, weight: str = "display") -> Face:
    """Resolve a logical family and weight to a loaded face, with fallbacks."""
    families = [family] + [f for f in FAMILY_FALLBACK if f != family]
    candidates = (
        spec
        for fam in families
        for w in (weight, "bold", "regular", "display")
        for spec in FAMILIES.get(fam, {}).get(w, ())
    )
    for spec in candidates:
        if path := _find(spec.file):
            try:
                return _face(path, spec.index, spec.axes)
            except Exception:
                continue
    raise MissingFontError(
        "no usable typeface found; the bundled faces in src/covers/fonts/ are missing"
    )


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
        """Visual height from cap line to last baseline."""
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
