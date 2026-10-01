"""Front-cover composition: the layout templates.

Everything is drawn in millimetre coordinates so the SVG viewBox is the physical
front panel with its bleed. :mod:`covers.formats` sizes the panel; this module only
decides where type and imagery sit inside it.

Type is placed off the cap line rather than the baseline, because that is what the
eye aligns to at display sizes.
"""

import io
import math
from dataclasses import asdict, dataclass, field, replace
from functools import cached_property
from typing import Any

from PIL import Image

from . import contrast, motifs
from .artdirection import ArtDirection
from .contrast import Block
from .formats import Geometry
from .lettering import Ink
from .palettes import (
    Palette,
    contrast_ratio,
    contrasting_ink,
    readable_on,
    relative_luminance,
)
from .svg import n as _n
from .svg import rect as _rect
from .typography import TextBlock, face, fit_display, umlaut_leading


@dataclass
class Content:
    title: str
    author: str
    genre_line: str = "Roman"
    imprint: str = ""

    def summary(self) -> dict[str, str]:
        """The copy the front prints."""
        return asdict(self)


@dataclass
class Ctx:
    geo: Geometry
    direction: ArtDirection
    content: Content
    seed: int
    #: The artwork itself (a PIL image covering the artwork plan), so type set on
    #: it can take its colour from what is actually underneath.
    artwork_image: Any = None
    #: Where the picture has to be toned for the type to read, found as the type is
    #: set (``compose``).
    burns: list[contrast.Burn] = field(default_factory=list)
    marks: bool = False

    @cached_property
    def palette(self) -> Palette:
        return self.direction.palette

    @property
    def margin(self) -> float:
        return margin(self.geo)

    @cached_property
    def fallback_ink(self) -> str:
        """Light or dark type, whichever suits the picture as a whole: one colour for
        every line whose planned colour does not read, so neighbours do not clash."""
        if self.artwork_sample is None:
            return readable_on(relative_luminance(self.palette.ground), _LIGHT, _DARK)
        return readable_on(contrast.median_tone(self.artwork_sample), _LIGHT, _DARK)

    @cached_property
    def artwork_sample(self) -> Any:
        """A small RGB copy of what is drawn in the artwork plan -- the picture, or
        the motif standing in for it on its ground -- to average colours from."""
        if self.artwork_image is not None:
            return contrast.sample(self.artwork_image)
        plan = artwork_plan(self.direction, self.geo)
        art = _artwork_or_motif(self, plan)
        if not art:
            return None
        from . import _cairo  # loads the native library; not needed to build SVG

        x, y, w, h = plan
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_n(x)} {_n(y)} {_n(w)} {_n(h)}">'
            f"{_rect(x, y, w, h, self.palette.ground)}{art}</svg>"
        )
        scale = contrast.SAMPLE_PX / max(w, h)
        png = _cairo.svg2png(svg.encode(), output_width=round(w * scale),
                             output_height=round(h * scale))
        return Image.open(io.BytesIO(png)).convert("RGB")


Rect = tuple[float, float, float, float]

#: House margin, as a fraction of the trim width. The artwork rect and the type
#: placement both derive from it, so it has to be one number.
MARGIN_RATIO = 0.085


def margin(geo: Geometry) -> float:
    return geo.panel_w_mm * MARGIN_RATIO


# --- Type placement helpers ---


def cased(text: str, mode: str) -> str:
    """Apply the brief's casing. German title case is left to the input."""
    return text.upper() if mode == "upper" else text


def _align_x(x: float, width: float, align: str) -> float:
    if align == "center":
        return x - width / 2
    if align == "right":
        return x - width
    return x


def draw_block(
    block: TextBlock,
    family: str,
    *,
    x: float,
    cap_top: float,
    align: str = "left",
    fill: str,
) -> tuple[str, float]:
    """Draw a fitted block from its cap line. Returns the SVG and the last baseline."""
    if not block.lines:
        return "", cap_top
    f = face(family, "display")
    out = []
    baseline = cap_top + f.cap_height * block.size
    for i, line in enumerate(block.lines):
        lx = _align_x(x, block.widths[i], align)
        y = baseline + i * block.leading * block.size
        frag = f.run(line, block.size, tracking=block.tracking, fill=fill, x=lx, y=y)
        out.append(frag)
    last = baseline + (len(block.lines) - 1) * block.leading * block.size
    return "".join(out), last


def draw_label(
    text: str,
    family: str,
    size: float,
    *,
    x: float,
    cap_top: float,
    align: str = "left",
    fill: str,
    tracking: float = 0.12,
    weight: str = "regular",
    max_width: float | None = None,
) -> tuple[str, float]:
    """A single tracked-out line, used for author lines, genre lines and imprints.
    Set smaller where it would run past ``max_width``."""
    if not text:
        return "", cap_top
    if max_width is not None:
        size = _label_size(text, family, size, max_width, tracking, weight)
    f = face(family, weight)
    lx = _align_x(x, f.measure(text, tracking) * size, align)
    baseline = cap_top + f.cap_height * size
    frag = f.run(text, size, tracking=tracking, fill=fill, x=lx, y=baseline)
    return frag, baseline


#: How the SVG names the artwork; ``render`` hands the rasteriser its bytes.
ARTWORK_HREF = "artwork:front"


def _image(uri: str, rect: Rect) -> str:
    x, y, w, h = rect
    return (
        f'<image x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
        f'preserveAspectRatio="xMidYMid slice" href="{uri}"/>'
    )


# Artwork placement -- decided before the image is generated so the image model
# can be asked for the right aspect ratio.


def artwork_plan(direction: ArtDirection, geo: Geometry) -> Rect | None:
    """Where artwork sits on the front canvas (bleed included), in mm."""
    if direction.artwork == "none" or direction.template == "type_block":
        return None
    b = geo.bleed_mm
    cw, ch = geo.front_bleed_w_mm, geo.front_bleed_h_mm
    pw, ph = geo.panel_w_mm, geo.panel_h_mm
    m = margin(geo)
    t = direction.template
    if t == "photo_duotone":
        return (0.0, 0.0, cw, b + ph * 0.62)
    if t == "kiwi_flat":
        top = b + ph * 0.52
        return (0.0, top, cw, ch - top)
    if t == "didone_centre":
        return (b + m, b + ph * 0.28, pw - 2 * m, ph * 0.40)
    return (0.0, 0.0, cw, ch)  # rororo_band, illustrated_full, picture: full bleed


def _artwork_or_motif(ctx: Ctx, rect: Rect | None) -> str:
    """Prefer generated artwork; fall back to the procedural motif in the same box."""
    if rect is None:
        return ""
    if ctx.artwork_image is not None:
        return _image(ARTWORK_HREF, rect)
    if ctx.direction.motif != "none":
        x, y, w, h = rect
        return motifs.draw(ctx.direction.motif, x, y, w, h, ctx.palette, ctx.seed)
    return ""


# Front templates. Each draws in front-canvas coordinates, origin at the top-left
# of the bleed box, and assumes the ground has already been laid down.


def _front_kiwi_flat(ctx: Ctx) -> str:
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    left, measure = b + m, pw - 2 * m
    fam = d.type_family
    out = []

    rect = artwork_plan(d, g)
    out.append(_artwork_or_motif(ctx, rect))

    author_size = pw * 0.040
    frag, author_base = draw_label(
        c.author.upper(),
        fam,
        author_size,
        max_width=measure,
        x=left,
        cap_top=b + m * 0.9,
        fill=p.ink,
        tracking=0.14,
        weight="bold",
    )
    out.append(frag)

    rule_y = author_base + author_size * 0.85
    out.append(_rect(left, rule_y, measure, max(0.35, pw * 0.0030), p.accent))

    title_top = rule_y + m * 0.6
    floor = (rect[1] if rect else b + ph - m * 2.2) - m * 0.5
    genre_size = pw * 0.032
    imprint_size = pw * 0.028
    # When the artwork runs to the foot, the genre line and the imprint both have
    # to stack above it, so the title's height budget has to reserve for both.
    stacked = rect is not None
    reserve = m * 0.55 + genre_size * 1.3
    if stacked:
        reserve += m * 0.5 + imprint_size * 1.3

    title = fit_display(
        cased(c.title, d.title_case),
        fam,
        max_width=measure,
        max_height=max(ph * 0.12, floor - title_top - reserve),
        max_lines=4,
        leading=0.86,
        tracking=-0.025,
    )
    frag, title_base = draw_block(
        title, fam, x=left, cap_top=title_top, fill=p.ink
    )
    out.append(frag)

    frag, genre_base = draw_label(
        c.genre_line.upper(),
        fam,
        genre_size,
        x=left,
        cap_top=title_base + m * 0.55,
        fill=p.accent,
        tracking=0.18,
        weight="bold",
    )
    out.append(frag)

    frag, _ = draw_label(
        c.imprint.upper(),
        fam,
        imprint_size,
        x=left,
        cap_top=(
            genre_base + m * 0.5 if stacked else b + ph - m - imprint_size
        ),
        fill=p.ink,
        tracking=0.2,
        weight="bold",
    )
    out.append(frag)
    return "".join(out)


def _front_rororo_band(ctx: Ctx) -> str:
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    cw = g.front_bleed_w_mm
    fam = d.type_family
    out = []

    rect = artwork_plan(d, g)
    out.append(_artwork_or_motif(ctx, rect))

    band_top = b + ph * 0.45
    band_h = ph * 0.30
    out.append(_rect(0, band_top, cw, band_h, p.accent))
    band_ink = contrasting_ink(p.accent, p.ink, p.ground)

    author_size = pw * 0.050
    author_ink = contrasting_ink(p.ground, p.ink, p.accent)
    frag, _ = draw_label(
        c.author.upper(),
        fam,
        author_size,
        max_width=pw - 2 * m,
        x=b + pw / 2,
        cap_top=band_top - m * 0.7 - author_size,
        align="center",
        fill=author_ink,
        tracking=0.16,
        weight="bold",
    )
    out.append(frag)

    pad = m * 0.5
    title = fit_display(
        cased(c.title, d.title_case),
        fam,
        max_width=pw - 2 * m * 0.7,
        max_height=band_h - 2 * pad,
        max_lines=3,
        leading=0.9,
        tracking=-0.02,
    )
    visual_h = title.height + face(fam, "display").cap_height * title.size
    frag, _ = draw_block(
        title,
        fam,
        x=b + pw / 2,
        cap_top=band_top + (band_h - visual_h) / 2,
        align="center",
        fill=band_ink,
    )
    out.append(frag)

    frag, _ = draw_label(
        c.genre_line.upper(),
        fam,
        pw * 0.032,
        x=b + pw / 2,
        cap_top=band_top + band_h + m * 0.6,
        align="center",
        fill=author_ink,
        tracking=0.2,
        weight="bold",
    )
    out.append(frag)

    imprint_size = pw * 0.028
    frag, _ = draw_label(
        c.imprint.upper(),
        fam,
        imprint_size,
        x=b + pw / 2,
        cap_top=b + ph - m - imprint_size,
        align="center",
        fill=author_ink,
        tracking=0.22,
        weight="bold",
    )
    out.append(frag)
    return "".join(out)


def _front_type_block(ctx: Ctx) -> str:
    """Display type filling the whole measure, each line set to its own size."""
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    left, measure = b + m, pw - 2 * m
    fam = d.type_family
    f = face(fam, "display")
    out = []

    author_size = pw * 0.042
    frag, author_base = draw_label(
        c.author.upper(),
        fam,
        author_size,
        max_width=measure,
        x=left,
        cap_top=b + m * 0.9,
        fill=p.accent,
        tracking=0.14,
        weight="bold",
    )
    out.append(frag)

    genre_size = pw * 0.032
    genre_top = b + ph - m - genre_size * 1.4
    words = [w for w in cased(c.title, d.title_case).split() if w]
    if not words:
        words = ["OHNE", "TITEL"]
    max_lines = min(5, len(words))
    per = -(-len(words) // max_lines)
    lines = [" ".join(words[i : i + per]) for i in range(0, len(words), per)]

    tracking = -0.02
    sizes = [measure / max(1e-6, f.measure(ln, tracking)) for ln in lines]
    leading = umlaut_leading(lines, 0.92)
    total = sum(s * leading for s in sizes)
    available = genre_top - (author_base + m * 0.8)
    if total > available:
        shrink = available / total
        sizes = [s * shrink for s in sizes]

    y = author_base + m * 0.8
    for line, size in zip(lines, sizes):
        w = f.measure(line, tracking) * size
        frag = f.run(
            line, size, tracking=tracking, fill=p.ink, x=left, y=y + f.cap_height * size
        )
        out.append(frag)
        if w < measure * 0.995:  # a short last line gets a rule to the margin
            out.append(
                _rect(
                    left + w + size * 0.12,
                    y + f.cap_height * size - size * 0.18,
                    max(0.0, measure - w - size * 0.12),
                    max(0.35, size * 0.06),
                    p.accent,
                )
            )
        y += size * leading

    frag, _ = draw_label(
        c.genre_line.upper(),
        fam,
        genre_size,
        x=left,
        cap_top=genre_top,
        fill=p.accent,
        tracking=0.2,
        weight="bold",
    )
    out.append(frag)

    imprint_size = pw * 0.028
    frag, _ = draw_label(
        c.imprint.upper(),
        fam,
        imprint_size,
        x=b + pw - m,
        cap_top=genre_top,
        align="right",
        fill=p.ink,
        tracking=0.22,
        weight="bold",
    )
    out.append(frag)
    return "".join(out)


def _front_didone_centre(ctx: Ctx) -> str:
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    left, measure = b + m, pw - 2 * m
    centre = b + pw / 2
    fam = d.type_family
    out = []

    hair = max(0.25, pw * 0.0018)
    for y in (b + m * 0.75, b + ph - m * 0.75):
        out.append(_rect(left, y, measure, hair, p.accent))
        out.append(_rect(left, y + hair * 3.5, measure, hair, p.accent))

    author_size = pw * 0.038
    frag, author_base = draw_label(
        c.author.upper(),
        fam,
        author_size,
        max_width=measure,
        x=centre,
        cap_top=b + m * 1.7,
        align="center",
        fill=p.ink,
        tracking=0.24,
    )
    out.append(frag)

    title = fit_display(
        cased(c.title, d.title_case),
        fam,
        max_width=measure * 0.92,
        max_height=ph * 0.22,
        max_lines=4,
        leading=1.04,
        tracking=0.0,
    )
    frag, title_base = draw_block(
        title,
        fam,
        x=centre,
        cap_top=author_base + m * 0.9,
        align="center",
        fill=p.ink,
    )
    out.append(frag)

    genre_size = pw * 0.030
    frag, genre_base = draw_label(
        c.genre_line,
        fam,
        genre_size,
        x=centre,
        cap_top=title_base + m * 0.7,
        align="center",
        fill=p.accent,
        tracking=0.08,
        weight="italic",
    )
    out.append(frag)

    rect = artwork_plan(d, g)
    if rect:
        x, y, w, h = rect
        y = genre_base + m * 0.9
        h = min(h, b + ph - m * 2.4 - y)
        if h > m:
            out.append(_artwork_or_motif(ctx, (x, y, w, h)))

    imprint_size = pw * 0.026
    frag, _ = draw_label(
        c.imprint.upper(),
        fam,
        imprint_size,
        x=centre,
        cap_top=b + ph - m * 1.45 - imprint_size,
        align="center",
        fill=p.ink,
        tracking=0.28,
    )
    out.append(frag)
    return "".join(out)


def _front_photo_duotone(ctx: Ctx) -> str:
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    left, measure = b + m, pw - 2 * m
    fam = d.type_family
    out = []

    rect = artwork_plan(d, g)
    out.append(_artwork_or_motif(ctx, rect))
    art_bottom = rect[1] + rect[3] if rect else b + ph * 0.6

    author_size = pw * 0.042
    frag, author_base = draw_label(
        c.author.upper(),
        fam,
        author_size,
        max_width=measure,
        x=left,
        cap_top=art_bottom + m * 0.7,
        fill=p.ink,
        tracking=0.14,
        weight="bold",
    )
    out.append(frag)

    genre_size = pw * 0.032
    imprint_size = pw * 0.028
    floor = b + ph - m - imprint_size * 1.2 - genre_size * 2.0
    title_top = author_base + m * 0.5
    title = fit_display(
        cased(c.title, d.title_case),
        fam,
        max_width=measure,
        max_height=max(ph * 0.08, floor - title_top),
        max_lines=3,
        leading=0.88,
        tracking=-0.025,
    )
    frag, title_base = draw_block(
        title, fam, x=left, cap_top=title_top, fill=p.ink
    )
    out.append(frag)

    frag, _ = draw_label(
        c.genre_line.upper(),
        fam,
        genre_size,
        x=left,
        cap_top=title_base + m * 0.5,
        fill=p.accent,
        tracking=0.2,
        weight="bold",
    )
    out.append(frag)

    frag, _ = draw_label(
        c.imprint.upper(),
        fam,
        imprint_size,
        x=b + pw - m,
        cap_top=b + ph - m - imprint_size,
        align="right",
        fill=p.ink,
        tracking=0.22,
        weight="bold",
    )
    out.append(frag)
    return "".join(out)


def _front_illustrated_full(ctx: Ctx) -> str:
    """The illustration runs the whole cover; the type sits in a panel over it.

    The panel is what makes an illustrated cover readable without asking the image
    model to leave a convenient empty corner, which it will not reliably do. It is
    also the idiom: a cartouche holding the type over a full-bleed picture.
    """
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    fam = d.type_family
    out = []

    rect = artwork_plan(d, g)
    out.append(_artwork_or_motif(ctx, rect))

    # Panel geometry: inset from the trim, sitting low so the picture keeps the
    # upper two thirds, which is where an illustration's subject usually lives.
    pad = m * 0.72
    panel_x = b + m * 0.62
    panel_w = pw - 2 * m * 0.62
    measure = panel_w - 2 * pad

    author_size = pw * 0.036
    genre_size = pw * 0.030
    imprint_size = pw * 0.026

    title = fit_display(
        cased(c.title, d.title_case),
        fam,
        max_width=measure,
        max_height=ph * 0.20,
        max_lines=3,
        leading=0.98,
        tracking=-0.01,
    )
    fd = face(fam, "display")
    title_h = title.height + fd.cap_height * title.size

    panel_h = (
        pad
        + author_size * 1.9
        + title_h
        + genre_size * 2.1
        + imprint_size * 1.9
        + pad
    )
    panel_y = b + ph - m * 0.62 - panel_h
    out.append(_rect(panel_x, panel_y, panel_w, panel_h, p.ground))

    y = panel_y + pad
    frag, author_base = draw_label(
        c.author.upper(),
        fam,
        author_size,
        max_width=measure,
        x=panel_x + pad,
        cap_top=y,
        fill=p.accent,
        tracking=0.16,
        weight="bold",
    )
    out.append(frag)

    frag, title_base = draw_block(
        title,
        fam,
        x=panel_x + pad,
        cap_top=author_base + author_size * 0.8,
        fill=p.ink,
    )
    out.append(frag)

    frag, genre_base = draw_label(
        c.genre_line,
        fam,
        genre_size,
        x=panel_x + pad,
        cap_top=title_base + genre_size * 1.1,
        fill=p.accent,
        tracking=0.06,
        weight="italic",
    )
    out.append(frag)

    frag, _ = draw_label(
        c.imprint.upper(),
        fam,
        imprint_size,
        x=panel_x + panel_w - pad,
        cap_top=genre_base + imprint_size * 0.9,
        align="right",
        fill=p.ink,
        tracking=0.2,
        weight="bold",
    )
    out.append(frag)
    return "".join(out)


#: Light and dark type, for where the planned colour does not read.
_LIGHT, _DARK = "#fbf8f3", "#161412"
#: The WCAG contrast type needs against the picture: display type, small lines. A
#: notch above the minimum for text on flat colour, since a picture is never flat.
TITLE_CONTRAST, TEXT_CONTRAST = 4.5, 7.0


def _paint(ctx: Ctx, ink: Ink, block: Block, need: float, uid: str) -> tuple[str, str]:
    """The ``(defs, fill)`` for type in ``ink`` at ``block``.

    The planned colour -- or gradient, if both its stops read -- where it reaches
    ``need`` against the picture underneath; otherwise the cover's one fallback
    colour. Where even that falls short, the picture is toned under the block
    (``contrast.Burn``) until it does: the type always reads, and nothing is put
    behind it.
    """
    plan = artwork_plan(ctx.direction, ctx.geo)
    found = contrast.tones(ctx.artwork_sample, block, plan) if ctx.artwork_sample is not None else None
    tones = found or (relative_luminance(ctx.palette.ground),) * 2

    def worst(colour: str) -> float:
        return min(contrast_ratio(relative_luminance(colour), t) for t in tones)

    if ink.gradient_to and min(worst(ink.color), worst(ink.gradient_to)) >= need:
        x, y, w, h = block.bbox()
        x2, y2 = {"down": (x, y + h), "across": (x + w, y), "diagonal": (x + w, y + h)}[ink.gradient]
        defs = (
            f'<defs><linearGradient id="{uid}" gradientUnits="userSpaceOnUse" '
            f'x1="{_n(x)}" y1="{_n(y)}" x2="{_n(x2)}" y2="{_n(y2)}">'
            f'<stop offset="0" stop-color="{ink.color}"/><stop offset="1" stop-color="{ink.gradient_to}"/>'
            "</linearGradient></defs>"
        )
        return defs, f"url(#{uid})"
    colour = ink.color if worst(ink.color) >= need else ctx.fallback_ink
    if worst(colour) < need:
        ctx.burns.append(contrast.Burn(block, relative_luminance(colour), need))
    return "", colour


#: How much of the panel height the title may take, per lettering size.
_TITLE_SHARE = {"small": 0.14, "medium": 0.2, "large": 0.28, "dominant": 0.38}
#: A left or right column's share of the measure.
_COLUMN = 0.46
#: Tracking and weight of the author, genre and imprint lines.
_AUTHOR, _GENRE, _IMPRINT = (0.08, "bold"), (0.06, "italic"), (0.2, "bold")


def _label_size(text: str, family: str, size: float, measure: float, tracking: float, weight: str) -> float:
    """``size``, or smaller where a single line would overrun ``measure``."""
    width = face(family, weight).measure(text, tracking)
    return min(size, measure / width) if width else size


def _inks(ctx: Ctx) -> tuple[Ink, Ink]:
    """The lettering's title and text colours, the brief's ink where it names none."""
    lt, ink = ctx.direction.lettering, Ink(color=ctx.direction.ink)
    return lt.title_ink or ink, lt.text_ink or ink


def _front_picture(ctx: Ctx) -> str:
    """The picture is the whole cover; the type is set straight onto it.

    No panel, band or plate. The brief's ``lettering`` says where the title goes --
    across the top or bottom, in a column down one side, or on a rising diagonal --
    whether the author stands with it or apart at the top or bottom edge, how large,
    and in what colour. Every line is checked against the pixels under it and made
    to read (``_paint``). The imprint sits small at the foot, as on the house covers.
    """
    g, d, c, lt = ctx.geo, ctx.direction, ctx.content, ctx.direction.lettering
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    fam = d.type_family
    ctx.burns.clear()
    title_ink, text_ink = _inks(ctx)
    column = lt.location in ("left", "right")
    measure = (pw - 2 * m) * (_COLUMN if column else 1)
    align = lt.location if column else "center" if lt.location == "diagonal" else lt.align
    x = {"left": b + m, "center": b + pw / 2, "right": b + pw - m}[align]
    author_size = _label_size(c.author, fam, pw * 0.046, measure, *_AUTHOR)
    genre_size = _label_size(c.genre_line, fam, pw * 0.032, measure, *_GENRE)
    imprint_size = pw * 0.024
    imprint_top = b + ph - m * 0.9 - imprint_size
    out = [_artwork_or_motif(ctx, artwork_plan(d, g))]

    def slot(edge: str, size: float) -> float:
        """The cap line of a line of ``size`` at the top edge, or the bottom one above the imprint."""
        return b + m * 1.1 if edge == "top" else imprint_top - m * 0.8 - size

    def line(name: str, text: str, size: float, top: float, ink: Ink, tracking: float, weight: str,
             at_x: float = x, at_align: str = align) -> float:
        width = face(fam, weight).measure(text, tracking) * size
        block = Block.upright(_align_x(at_x, width, at_align), top - size * 0.15, width, size * 1.15)
        defs, fill = _paint(ctx, ink, block, TEXT_CONTRAST, f"{name}-ink")
        frag, base = draw_label(text, fam, size, x=at_x, cap_top=top, align=at_align, fill=fill,
                                tracking=tracking, weight=weight)
        out.extend((defs, frag))
        return base

    author_at = lt.author_location
    if author_at != "with_title":
        line("author", c.author, author_size, slot(author_at, author_size), text_ink, *_AUTHOR)

    if lt.location == "diagonal":
        # Between the author's edge and the other one, which only the imprint uses.
        top = slot("top", 0) + (author_size * 2.2 if author_at == "top" else 0)
        bottom = slot("bottom", author_size) - author_size * 1.2 if author_at == "bottom" else imprint_top - m
        out.append(_diagonal_title(ctx, (top, bottom), genre_size))
    else:
        title = fit_display(
            cased(c.title, d.title_case), fam, max_width=measure,
            max_height=ph * _TITLE_SHARE[lt.size] * (1.4 if column else 1), max_lines=5 if column else 3,
            leading=0.98, tracking=-0.01,
        )
        title_h = title.height + face(fam, "display").cap_height * title.size
        with_author = author_at == "with_title"
        stack_h = (author_size * 1.9 if with_author else 0) + title_h + genre_size * 2.2
        top = imprint_top - m * 1.4 - stack_h if lt.location == "bottom" else slot("top", 0)
        if with_author:
            top = line("author", c.author, author_size, top, text_ink, *_AUTHOR) + author_size * 0.9
        width = max(title.widths, default=0)
        block = Block.upright(_align_x(x, width, align), top, width, title_h)
        defs, fill = _paint(ctx, title_ink, block, TITLE_CONTRAST, "title-ink")
        frag, title_base = draw_block(title, fam, x=x, cap_top=top, align=align, fill=fill)
        out.extend((defs, frag))
        line("genre", c.genre_line, genre_size, title_base + genre_size * 1.2, text_ink, *_GENRE)

    line("imprint", c.imprint.upper(), imprint_size, imprint_top, Ink(color=text_ink.color), *_IMPRINT,
         at_x=b + pw / 2, at_align="center")
    return "".join(out)


def _diagonal_title(ctx: Ctx, band: tuple[float, float], genre_size: float) -> str:
    """The title on a rising baseline with the genre line under it, centred in the
    ``band`` (top, bottom) between the lines above and below it."""
    g, d, c = ctx.geo, ctx.direction, ctx.content
    fam, centre, measure = d.type_family, g.bleed_mm + g.panel_w_mm / 2, g.panel_w_mm - 2 * ctx.margin
    title_ink, text_ink = _inks(ctx)
    theta = math.radians(-d.lettering.angle)
    cos, sin = math.cos(theta), math.sin(theta)

    title = fit_display(cased(c.title, d.title_case), fam, max_width=measure / cos,
                        max_height=g.panel_h_mm * _TITLE_SHARE[d.lettering.size],
                        max_lines=3, leading=0.98, tracking=-0.01)
    w = max(title.widths, default=0)
    h = title.height + face(fam, "display").cap_height * title.size + genre_size * 2.2
    # Fitted along the baseline; scaled down where the turned block would overrun
    # the measure or the band. Line breaks hold, since everything scales with size.
    k = min(1, measure / ((w * cos + h * sin) or 1), (band[1] - band[0]) / ((w * sin + h * cos) or 1))
    if k < 1:
        title = replace(title, size=title.size * k, widths=[v * k for v in title.widths])
        genre_size, w, h = genre_size * k, w * k, h * k

    cy = (band[0] + band[1]) / 2
    block = Block(centre, cy, w, h, d.lettering.angle)
    defs, fill = _paint(ctx, title_ink, block, TITLE_CONTRAST, "title-ink")
    genre_defs, genre_fill = _paint(ctx, text_ink, block, TEXT_CONTRAST, "genre-ink")
    lines, base = draw_block(title, fam, x=centre, cap_top=cy - h / 2, align="center", fill=fill)
    genre, _ = draw_label(c.genre_line, fam, genre_size, x=centre, cap_top=base + genre_size * 1.2,
                          align="center", fill=genre_fill, tracking=_GENRE[0], weight=_GENRE[1])
    rotate = f"rotate({d.lettering.angle} {_n(centre)} {_n(cy)})"
    return f'{defs}{genre_defs}<g transform="{rotate}">{lines}{genre}</g>'


FRONT_TEMPLATES = {
    "kiwi_flat": _front_kiwi_flat,
    "rororo_band": _front_rororo_band,
    "type_block": _front_type_block,
    "didone_centre": _front_didone_centre,
    "photo_duotone": _front_photo_duotone,
    "illustrated_full": _front_illustrated_full,
    "picture": _front_picture,
}


# --- Trim mark ---


HAIRLINE_MM = 0.2


def _trim_box(x: float, y: float, w: float, h: float) -> str:
    return (
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
        f'fill="none" stroke="#00A0A0" stroke-width="{_n(HAIRLINE_MM)}" '
        f'stroke-dasharray="2 2"/>'
    )


# --- Documents ---


def _svg(width_mm: float, height_mm: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_n(width_mm)}mm" height="{_n(height_mm)}mm" '
        f'viewBox="0 0 {_n(width_mm)} {_n(height_mm)}">{body}</svg>'
    )


def compose(ctx: Ctx) -> tuple[str, Any]:
    """The front as SVG, and the artwork it shows: toned where the type set on it
    needed that to read (``contrast.tone``)."""
    svg = build_front(ctx)
    img, plan = ctx.artwork_image, artwork_plan(ctx.direction, ctx.geo)
    if img is None or plan is None or not ctx.burns:
        return svg, img
    return svg, contrast.tone(img, ctx.burns, plan, feather=ctx.margin * 0.6)


def build_front(ctx: Ctx) -> str:
    """Front cover with bleed on all four edges."""
    g, p = ctx.geo, ctx.palette
    cw, ch = g.front_bleed_w_mm, g.front_bleed_h_mm
    body = _rect(0, 0, cw, ch, p.ground) + FRONT_TEMPLATES[ctx.direction.template](ctx)
    if ctx.marks:
        body += _trim_box(g.bleed_mm, g.bleed_mm, g.panel_w_mm, g.panel_h_mm)
    return _svg(cw, ch, body)
