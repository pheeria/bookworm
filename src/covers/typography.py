"""Typesetting engine: HarfBuzz shaping, outlined glyphs, and display fitting.

Type is shaped with HarfBuzz and emitted as outlined vector paths rather than SVG
``<text>`` elements. That keeps one source of truth for metrics -- the same shaped
advances drive line fitting, the SVG and the PNG -- and it means the
output carries no font dependency, which is what a repro house wants.
"""

import os
from dataclasses import dataclass
from functools import cache
from typing import Literal

import uharfbuzz as hb
from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

from . import settings

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


Faces = dict[str, tuple[FontSpec, ...]]
Casing = Literal["title", "upper", "never_upper"]


@dataclass(frozen=True)
class Family:
    """A logical type family: its faces per weight, and what the brief needs to know."""

    #: display, bold, regular and italic, each an ordered list of faces to try.
    faces: Faces
    #: One line for the art director: the face, and what it is for.
    blurb: str
    #: How its titles may be cased: sans display faces suit capitals, and a
    #: blackletter title in capitals is unreadable.
    casing: Casing = "title"


def _open(
    upright: str, italic: str | None = None, *, display: float = 700, bold: float = 700,
    regular: float = 400, opsz: dict[str, float] | None = None, title: str | None = None,
) -> Faces:
    """A bundled variable family at a weight per role.

    ``opsz`` gives optical sizes by role ("display", "bold", "regular"; italic
    takes the regular one); ``italic`` defaults to the upright for faces with no
    italic cut; ``title`` sets titles in a separate static face -- a display cut, or
    a companion the smaller lines cannot use.
    """
    def at(file: str, wght: float, role: str) -> tuple[FontSpec, ...]:
        size = {"opsz": opsz[role]} if opsz else {}
        return (_v(file, wght=wght, **size),)

    return {
        "display": (FontSpec(title),) if title else at(upright, display, "display"),
        "bold": at(upright, bold, "bold"),
        "regular": at(upright, regular, "regular"),
        "italic": at(italic or upright, regular, "regular"),
    }


def _static(display: str, *, bold: str | None = None, regular: str | None = None,
            italic: str | None = None) -> Faces:
    """A bundled family of static files. A role not given falls back to regular, and
    regular to display, so a one-file face is ``_static(file)``."""
    regular = regular or display
    return {role: (FontSpec(file),) for role, file in (
        ("display", display), ("bold", bold or regular), ("regular", regular),
        ("italic", italic or regular),
    )}


def _mac(file: str, open_faces: Faces, **index: int) -> Faces:
    """A macOS face first, by collection index per role, then the open face."""
    return {role: (FontSpec(file, index[role]), *open_faces[role]) for role in open_faces}


_LORA = _open("Lora[wght].ttf", "Lora-Italic[wght].ttf")
_OSWALD = "Oswald[wght].ttf"

# Logical families. The macOS faces come first where they exist -- Futura and
# Optima are German-publishing workhorses; Didot and Bodoni cover the neoclassical
# register of Insel and Manesse -- each with an open face of the same register
# behind it. The rest are open faces in their own right.
FAMILIES: dict[str, Family] = {
    "geometric": Family(
        _mac("Futura.ttc", {  # display is Condensed ExtraBold
            "display": (_v(_JOST, wght=800),), "bold": (_v(_JOST, wght=700),),
            "regular": (_v(_JOST, wght=400),), "italic": (_v(_JOST_I, wght=400),),
        }, display=4, bold=2, regular=0, italic=1),
        "Futura, the rororo/KiWi workhorse (open: Jost)", "upper",
    ),
    "grotesk": Family(
        _mac("HelveticaNeue.ttc", {  # display is Condensed Black
            "display": (_v(_ARCHIVO, wght=900, wdth=62),), "bold": (_v(_ARCHIVO, wght=700, wdth=100),),
            "regular": (_v(_ARCHIVO, wght=400, wdth=100),),
            "italic": (_v(_ARCHIVO_I, wght=400, wdth=100),),
        }, display=9, bold=1, regular=10, italic=2),
        "Helvetica Neue (open: Archivo)", "upper",
    ),
    "grotesk_condensed": Family(
        _mac("Avenir Next Condensed.ttc", {  # open: Nunito Sans at 75% width
            "display": (_v(_NUNITO, wght=900, wdth=75),), "bold": (_v(_NUNITO, wght=700, wdth=75),),
            "regular": (_v(_NUNITO, wght=400, wdth=75),), "italic": (_v(_NUNITO_I, wght=400, wdth=75),),
        }, display=8, bold=0, regular=5, italic=4),
        "a tall condensed sans, Avenir Next Condensed (open: Nunito Sans)", "upper",
    ),
    "neoclassical": Family(
        _mac("Didot.ttc", _open(_PLAYFAIR, _PLAYFAIR_I), display=2, bold=2, regular=0, italic=1),
        "Didot (open: Playfair Display)",
    ),
    # opsz 36, not the 96 maximum: at 96 the hairlines vanish in a cover shown
    # at thumbnail size, which is where most covers are seen.
    "didone": Family(
        _mac("Bodoni 72.ttc", _open(_BODONI, _BODONI_I, opsz={"display": 36, "bold": 36, "regular": 11}),
             display=2, bold=2, regular=0, italic=1),
        "Bodoni (open: Bodoni Moda)",
    ),
    "literary_serif": Family(
        _mac("Baskerville.ttc", _open(_BASKERVILLE, _BASKERVILLE_I), display=1, bold=1, regular=0, italic=2),
        "Baskerville, the Suhrkamp register (open: Libre Baskerville)",
    ),
    "humanist": Family(
        _mac("Optima.ttc", _static("AlegreyaSans-Black.ttf", bold="AlegreyaSans-Bold.ttf",
                                   regular="AlegreyaSans-Regular.ttf", italic="AlegreyaSans-Italic.ttf"),
             display=4, bold=1, regular=0, italic=2),  # display is ExtraBlack
        "Optima, Hermann Zapf (open: Alegreya Sans)",
    ),
    "slab": Family(
        _mac("SuperClarendon.ttc", _static("ZillaSlab-Bold.ttf", bold="ZillaSlab-Bold.ttf",
                                           regular="ZillaSlab-Regular.ttf", italic="ZillaSlab-Italic.ttf"),
             display=7, bold=5, regular=0, italic=1),  # display is Black
        "a Clarendon, Superclarendon (open: Zilla Slab)",
    ),
    "garalde": Family(
        _open(_GARAMOND, _GARAMOND_I, display=600),
        "Garamond, the literary-fiction face of Suhrkamp, Insel and Hanser (EB Garamond)",
    ),
    # Only the title is blackletter; author and imprint lines, set in tracked
    # capitals, would be unreadable in it, so they stay in Garamond.
    "fraktur": Family(
        _open(_GARAMOND, _GARAMOND_I, bold=600, title="UnifrakturMaguntia-Book.ttf"),
        "blackletter titles over Garamond, for fairy tales, legends and historical subjects",
        "never_upper",
    ),
    "meta": Family(
        _static("FiraSans-Black.ttf", bold="FiraSans-Bold.ttf", regular="FiraSans-Regular.ttf",
                italic="FiraSans-Italic.ttf"),
        "Spiekermann's humanist sans (Fira Sans), contemporary and non-fiction in feel", "upper",
    ),
    "cormorant": Family(
        _open("CormorantGaramond[wght].ttf", "CormorantGaramond-Italic[wght].ttf", regular=500),
        "an elegant display Garamond (Cormorant)",
    ),
    "crimson": Family(_open("CrimsonPro[wght].ttf", "CrimsonPro-Italic[wght].ttf"),
                      "a quiet, Minion-like book face (Crimson Pro)"),
    "lora": Family(_LORA, "a warm contemporary serif (Lora)"),
    "source_serif": Family(
        _open("SourceSerif4[opsz,wght].ttf", "SourceSerif4-Italic[opsz,wght].ttf",
              opsz={"display": 60, "bold": 20, "regular": 12}),
        "a sober transitional serif, for non-fiction (Source Serif)",
    ),
    "spectral": Family(
        _static("Spectral-ExtraBold.ttf", bold="Spectral-Bold.ttf", regular="Spectral-Regular.ttf",
                italic="Spectral-Italic.ttf"),
        "a fine literary serif (Spectral)",
    ),
    "fraunces": Family(
        _open("Fraunces[SOFT,WONK,opsz,wght].ttf", "Fraunces-Italic[SOFT,WONK,opsz,wght].ttf",
              opsz={"display": 144, "bold": 36, "regular": 14}),
        "a soft, wonky Old Style; very current (Fraunces)",
    ),
    "dm_serif": Family(_static("DMSerifDisplay-Regular.ttf", italic="DMSerifDisplay-Italic.ttf"),
                       "a high-contrast display serif (DM Serif Display)"),
    "caslon": Family(
        _open("LibreCaslonText[wght].ttf", "LibreCaslonText-Italic[wght].ttf",
              title="LibreCaslonDisplay-Regular.ttf"),
        "classic English Caslon, display cut over the text cut (Libre Caslon)",
    ),
    "alegreya": Family(_open("Alegreya[wght].ttf", "Alegreya-Italic[wght].ttf", display=800),
                       "a lively calligraphic serif (Alegreya)"),
    "newsreader": Family(
        _open("Newsreader[opsz,wght].ttf", "Newsreader-Italic[opsz,wght].ttf",
              opsz={"display": 72, "bold": 20, "regular": 12}),
        "a newspaper serif, for reportage and essays (Newsreader)",
    ),
    "young_serif": Family(_static("YoungSerif-Regular.ttf"), "a warm, plump serif (Young Serif)"),
    "gloock": Family(_static("Gloock-Regular.ttf"), "a bold high-contrast serif (Gloock)"),
    # Abril Fatface is for titles only; the lines beneath are set in Lora.
    "abril": Family({**_LORA, "display": (FontSpec("AbrilFatface-Regular.ttf"),)},
                    "a fat didone for titles, over Lora (Abril Fatface)"),
    "montserrat": Family(_open("Montserrat[wght].ttf", "Montserrat-Italic[wght].ttf", display=800),
                         "an urban geometric sans (Montserrat)", "upper"),
    "josefin": Family(_open("JosefinSans[wght].ttf", "JosefinSans-Italic[wght].ttf"),
                      "an art-deco geometric sans (Josefin Sans)", "upper"),
    "work_sans": Family(_open("WorkSans[wght].ttf", "WorkSans-Italic[wght].ttf", display=800),
                        "a friendly grotesk (Work Sans)", "upper"),
    "inter": Family(
        _open("Inter[opsz,wght].ttf", "Inter-Italic[opsz,wght].ttf", display=800,
              opsz={"display": 32, "bold": 14, "regular": 14}),
        "a neutral, precise sans (Inter)", "upper",
    ),
    "space_grotesk": Family(_open("SpaceGrotesk[wght].ttf"), "a quirky contemporary sans (Space Grotesk)",
                            "upper"),
    "syne": Family(_open("Syne[wght].ttf", display=800),
                   "an expressive fashion sans, very wide at display weight (Syne)", "upper"),
    "oswald": Family(_open(_OSWALD, bold=600), "a condensed gothic (Oswald)", "upper"),
    # Bebas Neue has capitals only and one weight; the smaller lines are in Oswald.
    "bebas": Family(_open(_OSWALD, bold=500, title="BebasNeue-Regular.ttf"),
                    "tall condensed capitals, over Oswald (Bebas Neue)", "upper"),
    "barlow_condensed": Family(
        _static("BarlowCondensed-Black.ttf", bold="BarlowCondensed-Bold.ttf",
                regular="BarlowCondensed-Regular.ttf", italic="BarlowCondensed-Italic.ttf"),
        "a DIN-like condensed sans (Barlow Condensed)", "upper",
    ),
    "grenze_gotisch": Family(_open("GrenzeGotisch[wght].ttf", bold=600),
                             "a modern blackletter, for historical subjects (Grenze Gotisch)", "never_upper"),
    "cinzel": Family(_open("Cinzel[wght].ttf", bold=600),
                     "Roman inscriptional capitals, for myth and fantasy (Cinzel)"),
}


def describe(families: tuple[str, ...]) -> str:
    """The families, one line each, as the brief's type_family description."""
    return "; ".join(f"{name}: {FAMILIES[name].blurb}" for name in families) + "."


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
    for spec in FAMILIES[family].faces[weight]:
        if path := _find(spec.file):
            return Face(_hb_face(path, spec.index), spec.axes)
    raise MissingFontError(
        f"no face for {family}/{weight}; the bundled fonts in {BUNDLED_DIR} are missing"
    )


def check_fonts() -> None:
    """Load every family and weight, so a missing face fails at startup, not mid-render."""
    for family, record in FAMILIES.items():
        for weight in record.faces:
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
    # Nothing measurable (only zero-width characters, say): nothing to set.
    return best or TextBlock([], 0.0, leading, tracking, [])
