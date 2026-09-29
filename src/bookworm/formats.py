"""German trade-book cover geometry.

Trim sizes are the customary formats used by German literary publishers, keyed by
the imprint they are most associated with. They are drawn from general book-trade
practice, not from any publisher's production spec sheet -- always reconcile
against the ``Umschlagvorgabe`` the publisher's production department sends you
before going to press.

Everything is computed in millimetres; ``px()`` converts at a given resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MM_PER_INCH = 25.4

Binding = Literal["taschenbuch", "paperback", "klappenbroschur", "hardcover"]

#: Beschnittzugabe -- bleed added to every outer edge of the printed sheet.
BLEED_MM = 3.0
#: Sicherheitsabstand -- keep live text and logos this far inside the trim.
SAFETY_MM = 5.0
#: Reserved white field for the EAN-13 barcode on the back cover (SC2 + quiet zone).
BARCODE_W_MM = 45.0
BARCODE_H_MM = 30.0


@dataclass(frozen=True)
class BookFormat:
    """A trim size plus the paper and case parameters needed to size a spine."""

    key: str
    label: str
    imprint: str
    trim_w_mm: float
    trim_h_mm: float
    binding: Binding
    #: Klappe width; 0 for formats without flaps.
    flap_mm: float = 0.0
    #: Werkdruckpapier grammage in g/m².
    grammage: float = 90.0
    #: Papiervolumen -- bulk factor of the book block paper.
    paper_volume: float = 1.2
    #: Graupappe thickness per board, hardcover only.
    board_mm: float = 0.0
    #: Überstand -- how far the case stands proud of the book block.
    overhang_mm: float = 0.0

    @property
    def aspect(self) -> float:
        return self.trim_h_mm / self.trim_w_mm


def _fmt(*args, **kwargs) -> tuple[str, BookFormat]:
    f = BookFormat(*args, **kwargs)
    return f.key, f


FORMATS: dict[str, BookFormat] = dict(
    [
        _fmt(
            "rororo_taschenbuch",
            "rororo Taschenbuch",
            "Rowohlt",
            118.0,
            190.0,
            "taschenbuch",
            grammage=80.0,
            paper_volume=1.3,
        ),
        _fmt(
            "rowohlt_paperback",
            "Paperback",
            "Rowohlt",
            135.0,
            205.0,
            "paperback",
            grammage=90.0,
            paper_volume=1.2,
        ),
        _fmt(
            "rowohlt_hardcover",
            "Hardcover mit Schutzumschlag",
            "Rowohlt",
            140.0,
            215.0,
            "hardcover",
            flap_mm=85.0,
            grammage=100.0,
            paper_volume=1.15,
            board_mm=2.5,
            overhang_mm=3.0,
        ),
        _fmt(
            "kiwi_paperback",
            "KiWi-Paperback",
            "Kiepenheuer & Witsch",
            125.0,
            190.0,
            "paperback",
            grammage=90.0,
            paper_volume=1.2,
        ),
        _fmt(
            "kiwi_taschenbuch",
            "KiWi-Taschenbuch",
            "Kiepenheuer & Witsch",
            125.0,
            200.0,
            "taschenbuch",
            grammage=80.0,
            paper_volume=1.3,
        ),
        _fmt(
            "kiwi_klappenbroschur",
            "Klappenbroschur",
            "Kiepenheuer & Witsch",
            135.0,
            215.0,
            "klappenbroschur",
            flap_mm=90.0,
            grammage=90.0,
            paper_volume=1.2,
        ),
        _fmt(
            "kiwi_hardcover",
            "Hardcover Leinen mit Schutzumschlag",
            "Kiepenheuer & Witsch",
            135.0,
            210.0,
            "hardcover",
            flap_mm=85.0,
            grammage=100.0,
            paper_volume=1.15,
            board_mm=2.5,
            overhang_mm=3.0,
        ),
        _fmt(
            "suhrkamp_taschenbuch",
            "suhrkamp taschenbuch",
            "Suhrkamp",
            108.0,
            177.0,
            "taschenbuch",
            grammage=80.0,
            paper_volume=1.3,
        ),
        _fmt(
            "din_a5_hardcover",
            "Hardcover DIN A5",
            "allgemein",
            148.0,
            210.0,
            "hardcover",
            flap_mm=85.0,
            grammage=100.0,
            paper_volume=1.15,
            board_mm=2.5,
            overhang_mm=3.0,
        ),
        _fmt(
            "grossformat_hardcover",
            "Großformatiges Hardcover",
            "allgemein",
            155.0,
            230.0,
            "hardcover",
            flap_mm=95.0,
            grammage=100.0,
            paper_volume=1.15,
            board_mm=3.0,
            overhang_mm=3.0,
        ),
    ]
)

DEFAULT_FORMAT = "kiwi_paperback"


def px(mm: float, dpi: int) -> int:
    """Millimetres to whole pixels at ``dpi``."""
    return max(1, round(mm / MM_PER_INCH * dpi))


def block_thickness_mm(fmt: BookFormat, pages: int) -> float:
    """Thickness of the book block itself (Buchblockstärke).

    ``pages`` counts printed pages, so the sheet count is half of it. Sheet
    thickness follows the German trade formula ``g/m² x Volumen / 1000``.
    """
    sheets = max(1, pages) / 2.0
    return sheets * (fmt.grammage * fmt.paper_volume / 1000.0)


def spine_mm(fmt: BookFormat, pages: int) -> float:
    """Rückenstärke of the printed cover or jacket.

    For a hardcover this is the jacket spine, which has to clear both boards
    plus a small allowance for the rounding of the spine.
    """
    spine = block_thickness_mm(fmt, pages)
    if fmt.binding == "hardcover":
        spine += 2 * fmt.board_mm + 2.0
    return round(spine, 1)


@dataclass(frozen=True)
class Panel:
    """One printable panel on the flat sheet, positioned in sheet coordinates."""

    name: Literal["back_flap", "back", "spine", "front", "front_flap"]
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float


@dataclass(frozen=True)
class Geometry:
    """Full print geometry for one cover, in millimetres."""

    fmt: BookFormat
    pages: int
    dpi: int
    panel_w_mm: float
    panel_h_mm: float
    spine_w_mm: float
    flap_mm: float
    bleed_mm: float
    safety_mm: float
    sheet_w_mm: float
    sheet_h_mm: float
    panels: tuple[Panel, ...] = field(default_factory=tuple)

    def panel(self, name: str) -> Panel:
        for p in self.panels:
            if p.name == name:
                return p
        raise KeyError(name)

    @property
    def front_bleed_w_mm(self) -> float:
        """Front panel including the bleed on its three outer edges."""
        return self.panel_w_mm + 2 * self.bleed_mm

    @property
    def front_bleed_h_mm(self) -> float:
        return self.panel_h_mm + 2 * self.bleed_mm

    def to_dict(self) -> dict:
        return {
            "format": {
                "key": self.fmt.key,
                "label": self.fmt.label,
                "imprint": self.fmt.imprint,
                "binding": self.fmt.binding,
                "trim_mm": [self.fmt.trim_w_mm, self.fmt.trim_h_mm],
            },
            "pages": self.pages,
            "dpi": self.dpi,
            "panel_mm": [self.panel_w_mm, self.panel_h_mm],
            "spine_mm": self.spine_w_mm,
            "flap_mm": self.flap_mm,
            "bleed_mm": self.bleed_mm,
            "safety_mm": self.safety_mm,
            "front_with_bleed_mm": [self.front_bleed_w_mm, self.front_bleed_h_mm],
            "front_with_bleed_px": [
                px(self.front_bleed_w_mm, self.dpi),
                px(self.front_bleed_h_mm, self.dpi),
            ],
            "sheet_mm": [self.sheet_w_mm, self.sheet_h_mm],
            "sheet_px": [px(self.sheet_w_mm, self.dpi), px(self.sheet_h_mm, self.dpi)],
            "panel_order": [p.name for p in self.panels],
        }


def geometry(fmt: BookFormat, pages: int = 288, dpi: int = 300) -> Geometry:
    """Lay the cover out flat: back flap | back | spine | front | front flap.

    A hardcover jacket is sized to the case rather than the block, so it gains
    the Überstand on the fore-edge of each panel and at head and foot.
    """
    spine = spine_mm(fmt, pages)
    panel_w = fmt.trim_w_mm + (fmt.overhang_mm if fmt.binding == "hardcover" else 0.0)
    panel_h = fmt.trim_h_mm + (2 * fmt.overhang_mm if fmt.binding == "hardcover" else 0.0)
    flap = fmt.flap_mm

    widths: list[tuple[str, float]] = []
    if flap:
        widths.append(("back_flap", flap))
    widths += [("back", panel_w), ("spine", spine), ("front", panel_w)]
    if flap:
        widths.append(("front_flap", flap))

    panels: list[Panel] = []
    x = BLEED_MM
    for name, w in widths:
        panels.append(Panel(name, x, BLEED_MM, w, panel_h))  # type: ignore[arg-type]
        x += w

    return Geometry(
        fmt=fmt,
        pages=pages,
        dpi=dpi,
        panel_w_mm=panel_w,
        panel_h_mm=panel_h,
        spine_w_mm=spine,
        flap_mm=flap,
        bleed_mm=BLEED_MM,
        safety_mm=SAFETY_MM,
        sheet_w_mm=x + BLEED_MM,
        sheet_h_mm=panel_h + 2 * BLEED_MM,
        panels=tuple(panels),
    )


def resolve_format(key: str | None) -> BookFormat:
    if not key:
        return FORMATS[DEFAULT_FORMAT]
    try:
        return FORMATS[key]
    except KeyError:
        raise KeyError(
            f"unknown format {key!r}; available: {', '.join(sorted(FORMATS))}"
        ) from None
