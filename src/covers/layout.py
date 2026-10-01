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

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from . import motifs
from .artdirection import ArtDirection
from .formats import Geometry
from .lettering import Ink
from .palettes import (
    Palette,
    contrast_ratio,
    contrasting_ink,
    linear,
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
    #: Where the picture has to be toned for the type to read (``toned_artwork``).
    burns: list["Burn"] = field(default_factory=list)
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
            tone = relative_luminance(self.palette.ground)
        else:
            tone = _percentile(self.artwork_sample.convert("L").histogram(), 0.5)
        return max((_LIGHT, _DARK), key=lambda c: contrast_ratio(relative_luminance(c), tone))

    @cached_property
    def artwork_sample(self) -> Any:
        """A small RGB copy of what is drawn in the artwork plan -- the picture, or
        the motif standing in for it on its ground -- to average colours from."""
        if self.artwork_image is not None:
            return ImageOps.contain(self.artwork_image, (_SAMPLE_PX, _SAMPLE_PX)).convert("RGB")
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
        scale = _SAMPLE_PX / max(w, h)
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
#: The long edge of the artwork copy the type's colours are sampled from.
_SAMPLE_PX = 256
#: The WCAG contrast type needs against the picture: display type, small lines. A
#: notch above the minimum for text on flat colour, since a picture is never flat.
TITLE_CONTRAST, TEXT_CONTRAST = 4.5, 7.0
#: The share of the pixels under a line that its colour has to read on.
_COVERED = 0.95


@dataclass(frozen=True)
class Turned:
    """A block of type turned about its centre: centre, size (mm) and angle (degrees)."""

    cx: float
    cy: float
    w: float
    h: float
    angle: float

    def corners(self, grow: float = 0.0) -> list[tuple[float, float]]:
        t = math.radians(self.angle)
        hw, hh = self.w / 2 + grow, self.h / 2 + grow
        return [
            (self.cx + dx * math.cos(t) - dy * math.sin(t), self.cy + dx * math.sin(t) + dy * math.cos(t))
            for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
        ]


@dataclass(frozen=True)
class Burn:
    """Tone the picture under ``rect`` (mm) until type of luminance ``ink`` reads at
    ``need``: darker under light type, lighter under dark type. For type that is not
    upright, ``turned`` is the block itself; ``rect`` is then its bounding box."""

    rect: Rect
    ink: float
    need: float
    turned: Turned | None = None


def _px_box(rect: Rect, plan: Rect, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """``rect`` (mm) in the pixels of an image covering ``plan``; None if outside it."""
    px, py, pw, ph = plan
    x, y, w, h = rect
    sx, sy = size[0] / pw, size[1] / ph
    box = (
        max(0, int((x - px) * sx)), max(0, int((y - py) * sy)),
        min(size[0], math.ceil((x + w - px) * sx)), min(size[1], math.ceil((y + h - py) * sy)),
    )
    return box if box[2] > box[0] and box[3] > box[1] else None


def _percentile(histogram: list[int], q: float) -> float:
    """The grey level below which a share ``q`` of the pixels fall, as relative luminance."""
    total, seen = sum(histogram), 0
    for level, count in enumerate(histogram):
        seen += count
        if seen >= q * total:
            return linear(level / 255)
    return 1.0


def _shape_mask(size: tuple[int, int], corners: list[tuple[float, float]]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(corners, fill=255)
    return mask


def _in_pixels(points: list[tuple[float, float]], plan: Rect, size: tuple[int, int], origin=(0, 0)):
    """Points (mm) in the pixels of an image covering ``plan``, relative to ``origin``."""
    sx, sy = size[0] / plan[2], size[1] / plan[3]
    return [((x - plan[0]) * sx - origin[0], (y - plan[1]) * sy - origin[1]) for x, y in points]


def _tones(img: Image.Image, rect: Rect, plan: Rect, turned: "Turned | None" = None) -> tuple[float, float] | None:
    """How dark and how light ``img`` (covering ``plan``) is under ``rect`` -- or under
    the ``turned`` block inside it: the relative luminance at either end of the
    pixels a line has to read on. None outside the picture."""
    box = _px_box(rect, plan, img.size)
    if box is None:
        return None
    crop = img.crop(box).convert("L")
    mask = _shape_mask(crop.size, _in_pixels(turned.corners(), plan, img.size, box[:2])) if turned else None
    histogram = crop.histogram(mask)
    if not sum(histogram):
        histogram = crop.histogram()
    return _percentile(histogram, 1 - _COVERED), _percentile(histogram, _COVERED)


def _tones_under(ctx: Ctx, rect: Rect, turned: "Turned | None" = None) -> tuple[float, float]:
    """``_tones`` of the cover's picture, or of its ground where there is no picture."""
    img, plan = ctx.artwork_sample, artwork_plan(ctx.direction, ctx.geo)
    found = _tones(img, rect, plan, turned) if img is not None and plan is not None else None
    if found is None:
        ground = relative_luminance(ctx.palette.ground)
        return ground, ground
    return found


def _paint(
    ctx: Ctx, ink: Ink, rect: Rect, need: float, uid: str, turned: Turned | None = None
) -> tuple[str, str]:
    """The ``(defs, fill)`` for type in ``ink`` over ``rect`` (mm).

    The planned colour -- or gradient, if both its stops read -- where it reaches
    ``need`` against the picture; otherwise the cover's one fallback colour. Where
    even that falls short, the picture is toned under the line (``Burn``) until it
    does: the type always reads, and nothing is put behind it.
    """
    tones = _tones_under(ctx, rect, turned)

    def worst(colour: str) -> float:
        return min(contrast_ratio(relative_luminance(colour), t) for t in tones)

    if ink.gradient_to and min(worst(ink.color), worst(ink.gradient_to)) >= need:
        x, y, w, h = rect
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
        ctx.burns.append(Burn(rect, relative_luminance(colour), need, turned))
    return "", colour


def _toned(img: Image.Image, light_type: bool, strength: float) -> Image.Image:
    """``img`` darkened (under light type) or lightened (under dark type) by ``strength``, 0 to 1."""
    if light_type:
        return ImageEnhance.Brightness(img).enhance(1 - strength)
    return Image.blend(img, Image.new("RGB", img.size, "white"), strength)


def _least_toning(sample: Image.Image, burn: Burn, block: Turned, plan: Rect) -> float:
    """The least toning of ``sample`` under ``block`` at which the burn's type reads; 0 if it does already."""

    def reads(strength: float) -> bool:
        tones = _tones(_toned(sample, burn.ink > 0.5, strength), burn.rect, plan, block)
        # A tenth over, for what resampling to and from the small copy loses.
        return tones is None or min(contrast_ratio(burn.ink, t) for t in tones) >= burn.need * 1.1

    if reads(0.0):
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(10):
        mid = (low + high) / 2
        low, high = (low, mid) if reads(mid) else (mid, high)
    return high


def toned_artwork(ctx: Ctx) -> Any:
    """The artwork with the picture toned under each line that needed it (``Burn``):
    as little as reaches the line's contrast, at full strength under the line and
    fading out beyond it, so no edge shows. Neighbouring lines' fades can overlap,
    so every line is measured again afterwards and topped up where it slipped."""
    img, plan = ctx.artwork_image, artwork_plan(ctx.direction, ctx.geo)
    if img is None or plan is None or not ctx.burns:
        return img
    img, feather = img.convert("RGB"), ctx.margin * 0.6
    for _ in range(3):
        toned_any = False
        for burn in ctx.burns:
            x, y, w, h = burn.rect
            block = burn.turned or Turned(x + w / 2, y + h / 2, w, h, 0)
            light_type = burn.ink > 0.5
            strength = _least_toning(ImageOps.contain(img, (_SAMPLE_PX, _SAMPLE_PX)), burn, block, plan)
            if not strength:
                continue
            outer = _px_box((x - 2 * feather, y - 2 * feather, w + 4 * feather, h + 4 * feather), plan, img.size)
            if outer is None:
                continue
            region = img.crop(outer)
            mask = _shape_mask(region.size, _in_pixels(block.corners(feather), plan, img.size, outer[:2]))
            mask = mask.filter(ImageFilter.GaussianBlur(feather * img.width / plan[2] / 2))
            img.paste(Image.composite(_toned(region, light_type, strength), region, mask), outer[:2])
            toned_any = True
        if not toned_any:
            break
    return img


#: How much of the panel height the title may take, per lettering size.
_TITLE_SHARE = {"small": 0.14, "medium": 0.2, "large": 0.28, "dominant": 0.38}
#: A left or right column's share of the measure.
_COLUMN = 0.46
#: Tracking and weight of the author and genre lines.
_AUTHOR, _GENRE = (0.08, "bold"), (0.06, "italic")


def _label_size(text: str, family: str, size: float, measure: float, tracking: float, weight: str) -> float:
    """``size``, or smaller where a single line would overrun ``measure``."""
    width = face(family, weight).measure(text, tracking)
    return min(size, measure / width) if width else size


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
    title_ink, text_ink = lt.title_ink or Ink(color=d.ink), lt.text_ink or Ink(color=d.ink)
    column = lt.location in ("left", "right")
    measure = (pw - 2 * m) * (_COLUMN if column else 1)
    align = lt.location if column else "center" if lt.location == "diagonal" else lt.align
    x = {"left": b + m, "center": b + pw / 2, "right": b + pw - m}[align]
    author_size = _label_size(c.author, fam, pw * 0.046, measure, *_AUTHOR)
    genre_size = _label_size(c.genre_line, fam, pw * 0.032, measure, *_GENRE)
    imprint_size = pw * 0.024
    imprint_top = b + ph - m * 0.9 - imprint_size
    title_text, share = cased(c.title, d.title_case), _TITLE_SHARE[lt.size]
    out = [_artwork_or_motif(ctx, artwork_plan(d, g))]

    def slot(edge: str, size: float) -> float:
        """The cap line of a line of ``size`` at the top edge, or the bottom one above the imprint."""
        return b + m * 1.1 if edge == "top" else imprint_top - m * 0.8 - size

    def line(name: str, text: str, size: float, top: float, ink: Ink, style: tuple[float, str],
             at: tuple[float, str] = (x, align)) -> float:
        tracking, weight = style
        width = face(fam, weight).measure(text, tracking) * size
        rect = (_align_x(at[0], width, at[1]), top - size * 0.15, width, size * 1.15)
        defs, fill = _paint(ctx, ink, rect, TEXT_CONTRAST, f"{name}-ink")
        frag, base = draw_label(text, fam, size, x=at[0], cap_top=top, align=at[1], fill=fill,
                                tracking=tracking, weight=weight)
        out.extend((defs, frag))
        return base

    # The author stands apart only at the end the title does not already occupy.
    title_end = {"top": "top", "left": "top", "right": "top", "bottom": "bottom"}.get(lt.location)
    author_at = lt.author_location
    if author_at == title_end:
        author_at = "with_title"
    if lt.location == "diagonal" and author_at == "with_title":
        author_at = "top"
    if author_at != "with_title":
        line("author", c.author, author_size, slot(author_at, author_size), text_ink, _AUTHOR)

    if lt.location == "diagonal":
        # Between the author's edge and the other one, which only the imprint uses.
        top = slot("top", 0) + (author_size * 2.2 if author_at == "top" else 0)
        bottom = slot("bottom", author_size) - author_size * 1.2 if author_at == "bottom" else imprint_top - m
        out.append(_diagonal_title(ctx, title_text, share, (top, bottom), title_ink, text_ink, genre_size))
    else:
        title = fit_display(
            title_text, fam, max_width=measure,
            max_height=ph * share * (1.4 if column else 1), max_lines=5 if column else 3,
            leading=0.98, tracking=-0.01,
        )
        title_h = title.height + face(fam, "display").cap_height * title.size
        with_author = author_at == "with_title"
        stack_h = (author_size * 1.9 if with_author else 0) + title_h + genre_size * 2.2
        top = imprint_top - m * 1.4 - stack_h if lt.location == "bottom" else slot("top", 0)
        if with_author:
            top = line("author", c.author, author_size, top, text_ink, _AUTHOR) + author_size * 0.9
        width = max(title.widths, default=0)
        defs, fill = _paint(ctx, title_ink, (_align_x(x, width, align), top, width, title_h),
                            TITLE_CONTRAST, "title-ink")
        frag, title_base = draw_block(title, fam, x=x, cap_top=top, align=align, fill=fill)
        out.extend((defs, frag))
        line("genre", c.genre_line, genre_size, title_base + genre_size * 1.2, text_ink, _GENRE)

    line("imprint", c.imprint.upper(), imprint_size, imprint_top, Ink(color=text_ink.color),
         (0.2, "bold"), at=(b + pw / 2, "center"))
    return "".join(out)


def _diagonal_title(
    ctx: Ctx, text: str, share: float, band: tuple[float, float], ink: Ink, text_ink: Ink, genre_size: float
) -> str:
    """The title on a rising baseline with the genre line under it, centred in the
    ``band`` (top, bottom) between the lines above and below it."""
    g, d, genre = ctx.geo, ctx.direction, ctx.content.genre_line
    fam, centre, measure = d.type_family, g.bleed_mm + g.panel_w_mm / 2, g.panel_w_mm - 2 * ctx.margin
    theta = math.radians(-d.lettering.angle)
    cos, sin = math.cos(theta), math.sin(theta)

    title = fit_display(text, fam, max_width=measure / cos, max_height=g.panel_h_mm * share,
                        max_lines=3, leading=0.98, tracking=-0.01)
    w = max(title.widths, default=0)
    title_h = title.height + face(fam, "display").cap_height * title.size
    h = title_h + genre_size * 2.2
    bw, bh = w * cos + h * sin, w * sin + h * cos
    # Fitted along the baseline; scaled down where the rotated block would overrun
    # the measure or the band. Line breaks hold, since everything scales with size.
    k = min(1, measure / (bw or 1), (band[1] - band[0]) / (bh or 1))
    if k < 1:
        title = replace(title, size=title.size * k, widths=[v * k for v in title.widths])
        title_h, genre_size, w, h, bw, bh = (v * k for v in (title_h, genre_size, w, h, bw, bh))

    cy = (band[0] + band[1]) / 2
    # Checked over the rotated block's bounding box, the area the lines cross.
    box = (centre - bw / 2, cy - bh / 2, bw, bh)
    # Any toning follows the block itself, turned with it, not its bounding box.
    turned = Turned(centre, cy, w, h, d.lettering.angle)
    defs, fill = _paint(ctx, ink, box, TITLE_CONTRAST, "title-ink", turned)
    genre_defs, genre_fill = _paint(ctx, text_ink, box, TEXT_CONTRAST, "genre-ink", turned)
    block, base = draw_block(title, fam, x=centre, cap_top=cy - h / 2, align="center", fill=fill)
    genre_line, _ = draw_label(genre, fam, genre_size, x=centre, cap_top=base + genre_size * 1.2,
                               align="center", fill=genre_fill, tracking=_GENRE[0], weight=_GENRE[1])
    rotate = f"rotate({d.lettering.angle} {_n(centre)} {_n(cy)})"
    return f'{defs}{genre_defs}<g transform="{rotate}">{block}{genre_line}</g>'


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


def build_front(ctx: Ctx) -> str:
    """Front cover with bleed on all four edges."""
    g, p = ctx.geo, ctx.palette
    cw, ch = g.front_bleed_w_mm, g.front_bleed_h_mm
    body = _rect(0, 0, cw, ch, p.ground) + FRONT_TEMPLATES[ctx.direction.template](ctx)
    if ctx.marks:
        body += _trim_box(g.bleed_mm, g.bleed_mm, g.panel_w_mm, g.panel_h_mm)
    return _svg(cw, ch, body)
