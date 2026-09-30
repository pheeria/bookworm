"""Front-cover composition: the layout templates.

Everything is drawn in millimetre coordinates so the SVG viewBox is the physical
front panel with its bleed. :mod:`covers.formats` sizes the panel; this module only
decides where type and imagery sit inside it.

Type is placed off the cap line rather than the baseline, because that is what the
eye aligns to at display sizes.
"""

import math
from dataclasses import asdict, dataclass, replace
from functools import cached_property
from typing import Any

from PIL import ImageOps

from . import motifs
from .artdirection import ArtDirection
from .formats import Geometry
from .lettering import Ink
from .palettes import Palette, contrasting_ink, luminance
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
    artwork_uri: str | None = None
    #: The artwork itself (a PIL image covering the artwork plan), so type set on
    #: it can take its colour from what is actually underneath.
    artwork_image: Any = None
    marks: bool = False

    @cached_property
    def palette(self) -> Palette:
        return self.direction.palette

    @property
    def margin(self) -> float:
        return margin(self.geo)

    @cached_property
    def artwork_sample(self) -> Any:
        """A small RGB copy of the artwork: averaging the colour under the type needs no more."""
        if self.artwork_image is None:
            return None
        return ImageOps.contain(self.artwork_image, (_SAMPLE_PX, _SAMPLE_PX)).convert("RGB")


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
) -> tuple[str, float]:
    """A single tracked-out line, used for author lines, genre lines and imprints."""
    if not text:
        return "", cap_top
    f = face(family, weight)
    lx = _align_x(x, f.measure(text, tracking) * size, align)
    baseline = cap_top + f.cap_height * size
    frag = f.run(text, size, tracking=tracking, fill=fill, x=lx, y=baseline)
    return frag, baseline


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
    if ctx.artwork_uri:
        return _image(ctx.artwork_uri, rect)
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


#: Light and dark type for a picture the palette's ink does not read on.
_LIGHT, _DARK = "#fbf8f3", "#161412"
#: The long edge of the artwork copy the type's colours are sampled from.
_SAMPLE_PX = 256


def _ground_under(ctx: Ctx, rect: Rect) -> str:
    """The average colour of the artwork under ``rect`` (mm), or the palette's ground."""
    img, plan = ctx.artwork_sample, artwork_plan(ctx.direction, ctx.geo)
    if img is None or plan is None:
        return ctx.palette.ground
    px, py, pw, ph = plan
    x, y, w, h = rect
    sx, sy = img.width / pw, img.height / ph
    box = (
        max(0, int((x - px) * sx)), max(0, int((y - py) * sy)),
        min(img.width, math.ceil((x + w - px) * sx)), min(img.height, math.ceil((y + h - py) * sy)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return ctx.palette.ground
    r, g, b = img.crop(box).resize((1, 1), 4).getpixel((0, 0))  # 4: BOX
    return f"#{r:02x}{g:02x}{b:02x}"


def _ink_on(ground: str, preferred: str, contrast: float = 0.4) -> str:
    """``preferred`` where it reads on ``ground``; otherwise light or dark type."""
    if abs(luminance(preferred) - luminance(ground)) >= contrast:
        return preferred
    return contrasting_ink(ground, _LIGHT, _DARK)


def _paint(ink: Ink, ground: str, box: Rect, uid: str) -> tuple[str, str]:
    """The ``(defs, fill)`` for type in ``ink`` over ``box`` (mm) on ``ground``.

    A gradient runs across the whole box, so a multi-line title shares one. Its
    stops may sit a little closer to the ground than a solid colour, since the eye
    reads the brighter end; where either would not read, the type falls back to one
    colour that does.
    """
    stops = (ink.color, ink.gradient_to)
    if not ink.gradient_to or any(_ink_on(ground, c, 0.28) != c for c in stops):
        return "", _ink_on(ground, ink.color)
    x, y, w, h = box
    x2, y2 = {"down": (x, y + h), "across": (x + w, y), "diagonal": (x + w, y + h)}[ink.gradient]
    defs = (
        f'<defs><linearGradient id="{uid}" gradientUnits="userSpaceOnUse" '
        f'x1="{_n(x)}" y1="{_n(y)}" x2="{_n(x2)}" y2="{_n(y2)}">'
        f'<stop offset="0" stop-color="{ink.color}"/><stop offset="1" stop-color="{ink.gradient_to}"/>'
        "</linearGradient></defs>"
    )
    return defs, f"url(#{uid})"


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

    No panel, band or plate: the brief's ``lettering`` says where the type goes --
    across the top or bottom, in a column down one side, or on a rising diagonal --
    how large, and in what colour, solid or a gradient. The image prompt kept that
    area calm; the colours are checked against the pixels actually under the type
    and give way to light or dark type where they would not read. The imprint sits
    small at the foot, as on the house covers.
    """
    g, d, c, lt = ctx.geo, ctx.direction, ctx.content, ctx.direction.lettering
    b, pw, ph, m = g.bleed_mm, g.panel_w_mm, g.panel_h_mm, ctx.margin
    fam = d.type_family
    title_ink, text_ink = lt.title_ink or Ink(color=d.ink), lt.text_ink or Ink(color=d.ink)
    column = lt.location in ("left", "right")
    measure = (pw - 2 * m) * (_COLUMN if column else 1)
    align = lt.location if column else "center" if lt.location == "diagonal" else lt.align
    x = {"left": b + m, "center": b + pw / 2, "right": b + pw - m}[align]
    left = x - {"left": 0, "center": measure / 2, "right": measure}[align]
    author_size = _label_size(c.author, fam, pw * 0.046, measure, *_AUTHOR)
    genre_size = _label_size(c.genre_line, fam, pw * 0.032, measure, *_GENRE)
    imprint_size = pw * 0.024
    imprint_top = b + ph - m * 0.9 - imprint_size
    title_text, share = cased(c.title, d.title_case), _TITLE_SHARE[lt.size]
    out = [_artwork_or_motif(ctx, artwork_plan(d, g))]

    def label(text: str, size: float, top: float, fill: str, style: tuple[float, str]) -> float:
        frag, base = draw_label(text, fam, size, x=x, cap_top=top, align=align, fill=fill,
                                tracking=style[0], weight=style[1])
        out.append(frag)
        return base

    if lt.location == "diagonal":
        author_top, genre_top = b + m * 1.1, imprint_top - m * 0.8 - genre_size
        for text, size, top, style in (
            (c.author, author_size, author_top, _AUTHOR), (c.genre_line, genre_size, genre_top, _GENRE),
        ):
            area = (b, top - m * 0.3, pw, size + m * 0.6)
            defs, fill = _paint(text_ink, _ground_under(ctx, area), area, f"{style[1]}-ink")
            out.append(defs)
            label(text, size, top, fill, style)
        band = (author_top + author_size * 2.2, genre_top - genre_size * 1.2)
        out.append(_diagonal_title(ctx, title_text, share, band, title_ink))
    else:
        title = fit_display(
            title_text, fam, max_width=measure,
            max_height=ph * share * (1.4 if column else 1), max_lines=5 if column else 3,
            leading=0.98, tracking=-0.01,
        )
        title_h = title.height + face(fam, "display").cap_height * title.size
        stack_h = author_size * 1.9 + title_h + genre_size * 2.2
        top = imprint_top - m * 1.4 - stack_h if lt.location == "bottom" else b + m * 1.1
        area = (left - m * 0.4, top - m * 0.4, measure + m * 0.8, stack_h + m * 0.8)
        ground = _ground_under(ctx, area)
        text_defs, text_fill = _paint(text_ink, ground, area, "text-ink")
        out.append(text_defs)
        title_top = label(c.author, author_size, top, text_fill, _AUTHOR) + author_size * 0.9
        title_defs, title_fill = _paint(title_ink, ground, (left, title_top, measure, title_h), "title-ink")
        frag, title_base = draw_block(title, fam, x=x, cap_top=title_top, align=align, fill=title_fill)
        out += [title_defs, frag]
        label(c.genre_line, genre_size, title_base + genre_size * 1.2, text_fill, _GENRE)

    imprint_ink = _ink_on(
        _ground_under(ctx, (0, imprint_top - m * 0.3, g.front_bleed_w_mm, imprint_size + m * 0.6)),
        text_ink.color,
    )
    frag, _ = draw_label(
        c.imprint.upper(), fam, imprint_size, x=b + pw / 2, cap_top=imprint_top,
        align="center", fill=imprint_ink, tracking=0.2, weight="bold",
    )
    out.append(frag)
    return "".join(out)


def _diagonal_title(ctx: Ctx, text: str, share: float, band: tuple[float, float], ink: Ink) -> str:
    """The title on a rising baseline, centred in the ``band`` (top, bottom) between
    the author and the genre line."""
    g, d = ctx.geo, ctx.direction
    fam, centre, measure = d.type_family, g.bleed_mm + g.panel_w_mm / 2, g.panel_w_mm - 2 * ctx.margin
    theta = math.radians(-d.lettering.angle)
    cos, sin = math.cos(theta), math.sin(theta)

    title = fit_display(text, fam, max_width=measure / cos, max_height=g.panel_h_mm * share,
                        max_lines=3, leading=0.98, tracking=-0.01)
    w = max(title.widths, default=0)
    h = title.height + face(fam, "display").cap_height * title.size
    bw, bh = w * cos + h * sin, w * sin + h * cos
    # Fitted along the baseline; scaled down where the rotated block would overrun
    # the measure or the band. Line breaks hold, since everything scales with size.
    k = min(1, measure / (bw or 1), (band[1] - band[0]) / (bh or 1))
    if k < 1:
        title = replace(title, size=title.size * k, widths=[v * k for v in title.widths])
        w, h, bw, bh = w * k, h * k, bw * k, bh * k

    cy = (band[0] + band[1]) / 2
    ground = _ground_under(ctx, (centre - bw / 2, cy - bh / 2, bw, bh))
    defs, fill = _paint(ink, ground, (centre - w / 2, cy - h / 2, w, h), "title-ink")
    block, _ = draw_block(title, fam, x=centre, cap_top=cy - h / 2, align="center", fill=fill)
    return f'{defs}<g transform="rotate({d.lettering.angle} {_n(centre)} {_n(cy)})">{block}</g>'


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
