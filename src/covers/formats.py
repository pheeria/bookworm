"""German trade-book cover geometry.

Trim sizes are the customary formats used by German literary publishers, keyed by
the imprint they are most associated with. They are drawn from general book-trade
practice, not from any publisher's production spec sheet -- always reconcile
against the ``Umschlagvorgabe`` the publisher's production department sends you
before going to press.

Everything is computed in millimetres; ``px()`` converts at a given resolution.
"""

from dataclasses import dataclass
from typing import Literal

MM_PER_INCH = 25.4

Binding = Literal["taschenbuch", "paperback", "klappenbroschur", "hardcover"]

#: Beschnittzugabe -- bleed added to every outer edge of the printed sheet.
BLEED_MM = 3.0
#: Sicherheitsabstand -- keep live text and logos this far inside the trim.
SAFETY_MM = 5.0


@dataclass(frozen=True)
class BookFormat:
    """A trim size, and how far a hardcover case stands proud of it."""

    key: str
    label: str
    imprint: str
    trim_w_mm: float
    trim_h_mm: float
    binding: Binding
    #: Überstand -- how far a hardcover case stands proud of the book block.
    overhang_mm: float = 0.0

    def summary(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "imprint": self.imprint,
            "binding": self.binding,
            "trim_mm": [self.trim_w_mm, self.trim_h_mm],
        }


FORMATS: dict[str, BookFormat] = {
    f.key: f
    for f in (
        BookFormat(
            "rororo_taschenbuch",
            "rororo Taschenbuch",
            "Rowohlt",
            118.0,
            190.0,
            "taschenbuch",
        ),
        BookFormat(
            "rowohlt_paperback",
            "Paperback",
            "Rowohlt",
            135.0,
            205.0,
            "paperback",
        ),
        BookFormat(
            "rowohlt_hardcover",
            "Hardcover mit Schutzumschlag",
            "Rowohlt",
            140.0,
            215.0,
            "hardcover",
            overhang_mm=3.0,
        ),
        BookFormat(
            "kiwi_paperback",
            "KiWi-Paperback",
            "Kiepenheuer & Witsch",
            125.0,
            190.0,
            "paperback",
        ),
        BookFormat(
            "kiwi_taschenbuch",
            "KiWi-Taschenbuch",
            "Kiepenheuer & Witsch",
            125.0,
            200.0,
            "taschenbuch",
        ),
        BookFormat(
            "kiwi_klappenbroschur",
            "Klappenbroschur",
            "Kiepenheuer & Witsch",
            135.0,
            215.0,
            "klappenbroschur",
        ),
        BookFormat(
            "kiwi_hardcover",
            "Hardcover Leinen mit Schutzumschlag",
            "Kiepenheuer & Witsch",
            135.0,
            210.0,
            "hardcover",
            overhang_mm=3.0,
        ),
        BookFormat(
            "suhrkamp_taschenbuch",
            "suhrkamp taschenbuch",
            "Suhrkamp",
            108.0,
            177.0,
            "taschenbuch",
        ),
        BookFormat(
            "din_a5_hardcover",
            "Hardcover DIN A5",
            "allgemein",
            148.0,
            210.0,
            "hardcover",
            overhang_mm=3.0,
        ),
        BookFormat(
            "grossformat_hardcover",
            "Großformatiges Hardcover",
            "allgemein",
            155.0,
            230.0,
            "hardcover",
            overhang_mm=3.0,
        ),
    )
}

DEFAULT_FORMAT = "kiwi_paperback"


def px(mm: float, dpi: int) -> int:
    return max(1, round(mm / MM_PER_INCH * dpi))


@dataclass(frozen=True)
class Geometry:
    """Print geometry for one front cover, in millimetres."""

    fmt: BookFormat
    dpi: int
    panel_w_mm: float
    panel_h_mm: float
    bleed_mm: float
    safety_mm: float

    @property
    def front_bleed_w_mm(self) -> float:
        """Front panel including the bleed on its outer edges."""
        return self.panel_w_mm + 2 * self.bleed_mm

    @property
    def front_bleed_h_mm(self) -> float:
        return self.panel_h_mm + 2 * self.bleed_mm

    def to_dict(self) -> dict:
        return {
            "format": self.fmt.summary(),
            "dpi": self.dpi,
            "panel_mm": [self.panel_w_mm, self.panel_h_mm],
            "bleed_mm": self.bleed_mm,
            "safety_mm": self.safety_mm,
            "front_with_bleed_mm": [self.front_bleed_w_mm, self.front_bleed_h_mm],
            "front_with_bleed_px": [
                px(self.front_bleed_w_mm, self.dpi),
                px(self.front_bleed_h_mm, self.dpi),
            ],
        }


def geometry(fmt: BookFormat, dpi: int = 300) -> Geometry:
    """Size the front panel.

    A hardcover jacket is sized to the case rather than the block, so it gains
    the Überstand on the fore-edge and at head and foot.
    """
    return Geometry(
        fmt=fmt,
        dpi=dpi,
        panel_w_mm=fmt.trim_w_mm + fmt.overhang_mm,
        panel_h_mm=fmt.trim_h_mm + 2 * fmt.overhang_mm,
        bleed_mm=BLEED_MM,
        safety_mm=SAFETY_MM,
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
