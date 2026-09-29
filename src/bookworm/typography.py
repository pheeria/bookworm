"""Typesetting engine: HarfBuzz shaping, outlined glyphs, and display fitting.

Type is shaped with HarfBuzz and emitted as outlined vector paths rather than SVG
``<text>`` elements. That keeps one source of truth for metrics -- the same shaped
advances drive line fitting, the SVG, the PNG and the PDF -- and it means the
output carries no font dependency, which is what a repro house wants.
"""

from __future__ import annotations

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
    index: int


# Logical families mapped onto the faces that ship with macOS. Futura, Palatino
# and Optima are the German-publishing workhorses; Didot and Bodoni cover the
# neoclassical register that Insel and Manesse live in.
FAMILIES: dict[str, dict[str, FontSpec]] = {
    "geometric": {  # Futura -- rororo, KiWi, Fischer
        "display": FontSpec("Futura.ttc", 4),  # Condensed ExtraBold
        "bold": FontSpec("Futura.ttc", 2),
        "regular": FontSpec("Futura.ttc", 0),
        "italic": FontSpec("Futura.ttc", 1),
    },
    "grotesk": {  # Helvetica Neue
        "display": FontSpec("HelveticaNeue.ttc", 9),  # Condensed Black
        "bold": FontSpec("HelveticaNeue.ttc", 1),
        "regular": FontSpec("HelveticaNeue.ttc", 10),
        "italic": FontSpec("HelveticaNeue.ttc", 2),
    },
    "grotesk_condensed": {  # Avenir Next Condensed
        "display": FontSpec("Avenir Next Condensed.ttc", 8),  # Heavy
        "bold": FontSpec("Avenir Next Condensed.ttc", 0),
        "regular": FontSpec("Avenir Next Condensed.ttc", 5),
        "italic": FontSpec("Avenir Next Condensed.ttc", 4),
    },
    "neoclassical": {  # Didot
        "display": FontSpec("Didot.ttc", 2),
        "bold": FontSpec("Didot.ttc", 2),
        "regular": FontSpec("Didot.ttc", 0),
        "italic": FontSpec("Didot.ttc", 1),
    },
    "didone": {  # Bodoni 72
        "display": FontSpec("Bodoni 72.ttc", 2),
        "bold": FontSpec("Bodoni 72.ttc", 2),
        "regular": FontSpec("Bodoni 72.ttc", 0),
        "italic": FontSpec("Bodoni 72.ttc", 1),
    },
    "literary_serif": {  # Baskerville
        "display": FontSpec("Baskerville.ttc", 1),
        "bold": FontSpec("Baskerville.ttc", 1),
        "regular": FontSpec("Baskerville.ttc", 0),
        "italic": FontSpec("Baskerville.ttc", 2),
    },
    "humanist": {  # Optima -- Hermann Zapf
        "display": FontSpec("Optima.ttc", 4),  # ExtraBlack
        "bold": FontSpec("Optima.ttc", 1),
        "regular": FontSpec("Optima.ttc", 0),
        "italic": FontSpec("Optima.ttc", 2),
    },
    "slab": {  # Superclarendon
        "display": FontSpec("SuperClarendon.ttc", 7),  # Black
        "bold": FontSpec("SuperClarendon.ttc", 5),
        "regular": FontSpec("SuperClarendon.ttc", 0),
        "italic": FontSpec("SuperClarendon.ttc", 1),
    },
}

#: Tried in order when a requested family has no usable face on this machine.
FAMILY_FALLBACK = (
    "geometric",
    "grotesk",
    "grotesk_condensed",
    "humanist",
    "literary_serif",
    "neoclassical",
    "didone",
    "slab",
)

TYPE_FAMILIES = tuple(FAMILIES)


class MissingFontError(RuntimeError):
    pass


class Face:
    """A single shaped-and-outlined font face."""

    def __init__(self, path: str, index: int) -> None:
        self.path = path
        self.index = index
        blob = hb.Blob.from_file_path(path)
        self._hb_face = hb.Face(blob, index)
        self._hb_font = hb.Font(self._hb_face)
        self.upem: int = self._hb_face.upem
        self._tt = TTFont(path, fontNumber=index, lazy=True)
        self._glyphset = self._tt.getGlyphSet()
        self._order = self._tt.getGlyphOrder()
        os2 = self._tt["OS/2"] if "OS/2" in self._tt else None
        hhea = self._tt["hhea"]
        self.ascender = hhea.ascent / self.upem
        self.descender = abs(hhea.descent) / self.upem
        cap = getattr(os2, "sCapHeight", 0) or 0
        self.cap_height = (cap / self.upem) if cap else self._measured_cap_height()
        xh = getattr(os2, "sxHeight", 0) or 0
        self.x_height = (xh / self.upem) if xh else self.cap_height * 0.72
        self._path_cache: dict[str, str] = {}

    def _measured_cap_height(self) -> float:
        try:
            glyf = self._tt["glyf"]
            g = glyf["H"]
            return (g.yMax or 0) / self.upem
        except Exception:
            return 0.7

    def _glyph_path(self, name: str) -> str:
        cached = self._path_cache.get(name)
        if cached is None:
            pen = SVGPathPen(self._glyphset)
            self._glyphset[name].draw(pen)
            cached = pen.getCommands()
            self._path_cache[name] = cached
        return cached

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
    ) -> tuple[str, float]:
        """Outline ``text`` as a single SVG path with its baseline on ``y``.

        Returns the SVG fragment and the advance width in the same units as ``size``.
        """
        glyphs = self.shape(text)
        pen = SVGPathPen(self._glyphset)
        # Outlines come back in font units, so the scale carries the 1/upem.
        unit = size / self.upem
        cursor = 0.0
        for name, adv, xo, yo in glyphs:
            if self._glyph_path(name):
                t = Transform().translate(
                    x + (cursor + xo) * size, y - yo * size
                ).scale(unit, -unit)
                self._glyphset[name].draw(TransformPen(pen, t))
            cursor += adv + tracking
        width = max(0.0, cursor - tracking) * size
        d = pen.getCommands()
        if not d:
            return "", width
        return f'<path d="{d}" fill="{fill}"/>', width


@lru_cache(maxsize=64)
def _face(path: str, index: int) -> Face:
    return Face(path, index)


@lru_cache(maxsize=256)
def face(family: str, weight: str = "display") -> Face:
    """Resolve a logical family and weight to a loaded face, with fallbacks."""
    order = [family] + [f for f in FAMILY_FALLBACK if f != family]
    for fam in order:
        specs = FAMILIES.get(fam)
        if not specs:
            continue
        for w in (weight, "bold", "regular", "display"):
            spec = specs.get(w)
            if not spec:
                continue
            path = _find(spec.file)
            if path:
                try:
                    return _face(path, spec.index)
                except Exception:
                    continue
    raise MissingFontError(
        "no usable typeface found; install the macOS supplemental fonts or drop "
        "TTF/OTF files into src/bookworm/fonts/"
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
) -> tuple[float, float, list[str]]:
    """Split ``words`` into ``n_lines`` minimising the widest line, then ragging.

    Returns ``(max_width_em, sum_sq_slack, lines)``. Word order is preserved, so
    this is a linear partition -- exhaustive DP is cheap at title lengths.
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

    max_w, sum_sq, splits = go(0, n_lines)
    lines, start = [], 0
    for end in splits:
        lines.append(" ".join(words[start:end]))
        start = end
    return max_w, sum_sq, lines


def fit_display(
    text: str,
    family: str,
    *,
    weight: str = "display",
    max_width: float,
    max_height: float,
    max_lines: int = 4,
    leading: float = 0.88,
    tracking: float = -0.02,
    max_size: float | None = None,
) -> TextBlock:
    """Set ``text`` as large as will fit the measure, choosing the line count.

    Tries every line count up to ``max_lines`` and keeps the one that yields the
    largest type; ties go to the fewest lines. Display type gets slightly negative
    tracking, which is what the size asks for optically.
    """
    words = [w for w in text.split() if w]
    if not words:
        return TextBlock([], 0.0, leading, tracking, [])
    f = face(family, weight)
    ceiling = max_size if max_size is not None else float("inf")

    best: TextBlock | None = None
    for n in range(1, min(max_lines, len(words)) + 1):
        max_w_em, _, lines = _partition(f, words, n, tracking)
        if max_w_em <= 0:
            continue
        lead = umlaut_leading(lines, leading)
        by_width = max_width / max_w_em
        # cap line down to the last baseline
        h_em = (n - 1) * lead + f.cap_height
        by_height = max_height / h_em if h_em > 0 else float("inf")
        size = min(by_width, by_height, ceiling)
        if best is None or size > best.size + 1e-9:
            widths = [f.measure(ln, tracking) * size for ln in lines]
            best = TextBlock(lines, size, lead, tracking, widths)
    assert best is not None
    return best


def wrap_body(
    text: str,
    family: str,
    size: float,
    max_width: float,
    *,
    weight: str = "regular",
    tracking: float = 0.0,
    max_lines: int | None = None,
) -> list[str]:
    """Greedy wrap for body copy at a fixed size, honouring existing paragraphs."""
    f = face(family, weight)
    lines: list[str] = []
    for para in text.split("\n"):
        words = [w for w in para.split() if w]
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if f.measure(candidate, tracking) * size <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = _ellipsise(f, lines[-1], size, max_width, tracking)
    return lines


def _ellipsise(f: Face, line: str, size: float, max_width: float, tracking: float) -> str:
    words = line.split()
    while words:
        candidate = " ".join(words) + " …"
        if f.measure(candidate, tracking) * size <= max_width:
            return candidate
        words.pop()
    return "…"


def letterspaced(
    text: str,
    family: str,
    size: float,
    *,
    weight: str = "regular",
    tracking: float = 0.12,
    fill: str = "#000000",
    x: float = 0.0,
    y: float = 0.0,
) -> tuple[str, float]:
    """A small tracked-out label, the way German covers set author lines and series marks."""
    return face(family, weight).run(
        text, size, tracking=tracking, fill=fill, x=x, y=y
    )
