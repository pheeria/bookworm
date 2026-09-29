"""Cover composition: five layout templates, a spine, a back cover and flaps.

Everything is drawn in millimetre coordinates so the SVG viewBox is the physical
sheet. Panels are positioned by :mod:`bookworm.formats`; this module only decides
where type and imagery sit inside them.

Type is placed off the cap line rather than the baseline, because that is what the
eye aligns to at display sizes.
"""

from dataclasses import dataclass, field

from . import motifs
from .artdirection import ArtDirection
from .formats import BARCODE_H_MM, BARCODE_W_MM, Geometry
from .palettes import Palette, contrasting_ink
from .svg import n as _n
from .svg import rect as _rect
from .typography import TextBlock, face, fit_display, umlaut_leading, wrap_body


@dataclass
class Content:
    title: str
    author: str
    genre_line: str = "Roman"
    blurb: str = ""
    imprint: str = ""
    isbn: str = ""
    price: str = ""
    translator: str = ""

    def summary(self) -> dict[str, str]:
        """The copy worth reporting back; isbn/price/translator are inputs."""
        return {
            "title": self.title,
            "author": self.author,
            "genre_line": self.genre_line,
            "blurb": self.blurb,
            "imprint": self.imprint,
        }


@dataclass
class Ctx:
    geo: Geometry
    direction: ArtDirection
    content: Content
    palette: Palette
    seed: int
    artwork_uri: str | None = None
    marks: bool = False
    spine_direction: str = "top_to_bottom"
    notes: list[str] = field(default_factory=list)

    @property
    def margin(self) -> float:
        return self.geo.panel_w_mm * MARGIN_RATIO


Rect = tuple[float, float, float, float]

#: House margin, as a fraction of the trim width. The artwork rect and the type
#: placement both derive from it, so it has to be one number.
MARGIN_RATIO = 0.085


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


def draw_paragraph(
    text: str,
    family: str,
    size: float,
    *,
    x: float,
    cap_top: float,
    measure: float,
    fill: str,
    leading: float = 1.5,
    max_lines: int | None = None,
) -> tuple[str, float]:
    """Body copy for the back cover and flaps."""
    if not text:
        return "", cap_top
    f = face(family, "regular")
    lines = wrap_body(text, family, size, measure, max_lines=max_lines)
    out = []
    baseline = cap_top + f.cap_height * size
    for i, line in enumerate(lines):
        if line:
            frag = f.run(line, size, fill=fill, x=x, y=baseline + i * leading * size)
            out.append(frag)
    return "".join(out), baseline + (len(lines) - 1) * leading * size


def _rule(x: float, y: float, w: float, thickness: float, fill: str) -> str:
    return _rect(x, y, w, thickness, fill)


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
    m = pw * MARGIN_RATIO
    t = direction.template
    if t == "photo_duotone":
        return (0.0, 0.0, cw, b + ph * 0.62)
    if t == "kiwi_flat":
        top = b + ph * 0.52
        return (0.0, top, cw, ch - top)
    if t == "didone_centre":
        return (b + m, b + ph * 0.28, pw - 2 * m, ph * 0.40)
    return (0.0, 0.0, cw, ch)  # rororo_band, illustrated_full: full bleed


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
    out.append(_rule(left, rule_y, measure, max(0.35, pw * 0.0030), p.accent))

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
        total = available

    y = author_base + m * 0.8
    for line, size in zip(lines, sizes):
        w = f.measure(line, tracking) * size
        frag = f.run(
            line, size, tracking=tracking, fill=p.ink, x=left, y=y + f.cap_height * size
        )
        out.append(frag)
        if w < measure * 0.995:  # a short last line gets a rule to the margin
            out.append(
                _rule(
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
        out.append(_rule(left, y, measure, hair, p.accent))
        out.append(_rule(left, y + hair * 3.5, measure, hair, p.accent))

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


FRONT_TEMPLATES = {
    "kiwi_flat": _front_kiwi_flat,
    "rororo_band": _front_rororo_band,
    "type_block": _front_type_block,
    "didone_centre": _front_didone_centre,
    "photo_duotone": _front_photo_duotone,
    "illustrated_full": _front_illustrated_full,
}


# --- Back cover, spine, flaps ---


def _teaser(text: str, sentences: int = 2) -> str:
    """The opening sentences, for when the full blurb lives on the flap."""
    parts = [s for s in text.replace("\n", " ").split(". ") if s.strip()]
    if not parts:
        return text
    head = ". ".join(parts[:sentences]).rstrip(".")
    return head + "."


def back_panel(ctx: Ctx, x: float, y: float) -> str:
    """Blurb, imprint, and a reserved white field for the EAN barcode.

    On a flapped format the full Klappentext belongs on the front flap, so the
    back carries only a teaser rather than repeating it.
    """
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    pw, ph, m = g.panel_w_mm, g.panel_h_mm, ctx.margin
    fam = d.type_family
    copy = _teaser(c.blurb) if g.flap_mm else c.blurb
    out = []

    body_size = pw * 0.030
    genre_size = pw * 0.028
    frag, genre_base = draw_label(
        c.genre_line.upper(),
        fam,
        genre_size,
        x=x + m,
        cap_top=y + m * 1.2,
        fill=p.accent,
        tracking=0.2,
        weight="bold",
    )
    out.append(frag)

    barcode_y = y + ph - m - BARCODE_H_MM
    max_lines = max(4, int((barcode_y - genre_base - m * 2.0) / (body_size * 1.5)))
    frag, blurb_base = draw_paragraph(
        copy,
        fam,
        body_size,
        x=x + m,
        cap_top=genre_base + m * 0.9,
        measure=pw - 2 * m,
        fill=p.ink,
        max_lines=max_lines,
    )
    out.append(frag)

    if c.translator:
        frag, _ = draw_label(
            c.translator,
            fam,
            pw * 0.024,
            x=x + m,
            cap_top=blurb_base + m * 0.9,
            fill=p.ink,
            tracking=0.04,
            weight="italic",
        )
        out.append(frag)

    # Barcode is reserved, not drawn: the printer drops in the real EAN film.
    out.append(_rect(x + pw - m - BARCODE_W_MM, barcode_y, BARCODE_W_MM, BARCODE_H_MM, "#FFFFFF"))
    label = " · ".join(v for v in (c.isbn, c.price) if v)
    if label:
        frag, _ = draw_label(
            label,
            fam,
            pw * 0.022,
            x=x + pw - m - BARCODE_W_MM,
            cap_top=barcode_y - pw * 0.030,
            fill=p.ink,
            tracking=0.06,
        )
        out.append(frag)

    # The imprint shares its line with the barcode field, so hold it to the
    # space left of it rather than letting a long publisher name run underneath.
    if c.imprint:
        available = pw - 2 * m - BARCODE_W_MM - m * 0.5
        em = face(fam, "bold").measure(c.imprint.upper(), 0.22)
        imprint_size = min(pw * 0.028, available / max(1e-6, em))
        frag, _ = draw_label(
            c.imprint.upper(),
            fam,
            imprint_size,
            x=x + m,
            cap_top=y + ph - m - imprint_size,
            fill=p.ink,
            tracking=0.22,
            weight="bold",
        )
        out.append(frag)
    return "".join(out)


def spine_panel(ctx: Ctx, x: float, y: float) -> str:
    """Set the spine if it is wide enough to hold type.

    German trade books are typically read head-to-foot, which is the default;
    ``spine_direction='bottom_to_top'`` gives the continental alternative.
    """
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    w, h, m = g.spine_w_mm, g.panel_h_mm, ctx.margin
    fam = d.type_family
    if w < 7.0:
        ctx.notes.append(
            f"spine is only {w:.1f} mm wide, too narrow to letter; left as flat colour"
        )
        return _rect(x, y, w, h, p.secondary)

    out = [_rect(x, y, w, h, p.secondary)]
    ink = contrasting_ink(p.secondary, p.ink, p.ground)
    fd = face(fam, "display")
    fr = face(fam, "bold")

    title = cased(c.title, d.title_case)
    author = c.author.upper()
    margin = m * 0.8
    imprint_size = min(w * 0.34, 3.2)
    # Keep the run clear of the imprint at the foot of the spine.
    foot = m * 0.7 + imprint_size * 2.2
    run_length = h - margin - foot

    # Size so that title + gap + author fits the run, and the type still clears
    # the spine width.
    title_em = fd.measure(title, -0.01)
    author_em = fr.measure(author, 0.1)
    gap_em = 0.9
    total_em = title_em + gap_em + author_em * 0.62
    title_size = min(w * 0.46, run_length / max(1e-6, total_em))
    author_size = title_size * 0.62

    title_w = title_em * title_size
    author_w = author_em * author_size

    top_to_bottom = ctx.spine_direction != "bottom_to_top"
    if top_to_bottom:
        # rotate(+90) is clockwise here: local +x runs down the spine from the
        # head, and the cap side of the glyphs faces the spine's right edge.
        transform = f"translate({_n(x + w)},{_n(y)}) rotate(90)"
        start = margin
        title_at = start
        author_at = start + title_w + title_size * gap_em
    else:
        # local +x runs up the spine from the foot, so the run starts above the
        # imprint rather than on top of it.
        transform = f"translate({_n(x)},{_n(y + h)}) rotate(-90)"
        start = foot
        title_at = start + author_w + author_size * gap_em
        author_at = start

    # Centre each run across the spine width: the baseline sits half a cap height
    # off centre, on the opposite side from the caps.
    inner = []
    frag = fd.run(
        title,
        title_size,
        tracking=-0.01,
        fill=ink,
        x=title_at,
        y=(w + fd.cap_height * title_size) / 2,
    )
    inner.append(frag)
    frag = fr.run(
        author,
        author_size,
        tracking=0.1,
        fill=ink,
        x=author_at,
        y=(w + fr.cap_height * author_size) / 2,
    )
    inner.append(frag)

    out.append(f'<g transform="{transform}">{"".join(inner)}</g>')

    # The foot imprint reads across the spine, so it has to fit the spine width.
    if c.imprint:
        pad = w * 0.12
        em = fr.measure(c.imprint.upper(), 0.1)
        foot_size = min(imprint_size, (w - 2 * pad) / max(1e-6, em))
        frag, _ = draw_label(
            c.imprint.upper(),
            fam,
            foot_size,
            x=x + w / 2,
            cap_top=y + h - m * 0.7 - foot_size,
            align="center",
            fill=ink,
            tracking=0.1,
            weight="bold",
        )
        out.append(frag)
    return "".join(out)


def flap_panel(ctx: Ctx, x: float, y: float, w: float, *, side: str) -> str:
    """Front flap carries the blurb tail; back flap the imprint line."""
    g, d, c, p = ctx.geo, ctx.direction, ctx.content, ctx.palette
    ph, m = g.panel_h_mm, ctx.margin * 0.7
    fam = d.type_family
    out = [_rect(x, y, w, ph, p.ground)]
    size = w * 0.055
    if side == "front_flap":
        frag, base = draw_label(
            c.author.upper(),
            fam,
            size,
            x=x + m,
            cap_top=y + m * 1.4,
            fill=p.accent,
            tracking=0.14,
            weight="bold",
        )
        out.append(frag)
        frag, _ = draw_paragraph(
            c.blurb,
            fam,
            size * 0.92,
            x=x + m,
            cap_top=base + m,
            measure=w - 2 * m,
            fill=p.ink,
            max_lines=int((ph - m * 4) / (size * 0.92 * 1.5)),
        )
        out.append(frag)
    else:
        frag, _ = draw_label(
            c.imprint.upper(),
            fam,
            size * 0.9,
            x=x + m,
            cap_top=y + ph - m * 1.4 - size,
            fill=p.ink,
            tracking=0.2,
            weight="bold",
        )
        out.append(frag)
    return "".join(out)


# --- Print marks ---


HAIRLINE_MM = 0.2


def _trim_box(x: float, y: float, w: float, h: float) -> str:
    return (
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
        f'fill="none" stroke="#00A0A0" stroke-width="{_n(HAIRLINE_MM)}" '
        f'stroke-dasharray="2 2"/>'
    )


def print_marks(ctx: Ctx) -> str:
    """Trim box and fold lines, outside the artwork, for proofing only."""
    g = ctx.geo
    out = [
        _trim_box(
            g.bleed_mm, g.bleed_mm,
            g.sheet_w_mm - 2 * g.bleed_mm, g.sheet_h_mm - 2 * g.bleed_mm,
        )
    ]
    for panel in g.panels:
        if panel.name in ("spine", "front", "front_flap"):
            out.append(
                f'<line x1="{_n(panel.x_mm)}" y1="0" x2="{_n(panel.x_mm)}" '
                f'y2="{_n(g.sheet_h_mm)}" stroke="#E000E0" stroke-width="{_n(HAIRLINE_MM)}" '
                f'stroke-dasharray="3 2"/>'
            )
    return "".join(out)


# --- Documents ---


def _svg(width_mm: float, height_mm: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
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


def build_spread(ctx: Ctx) -> str:
    """The full flat sheet: back flap | back | spine | front | front flap."""
    g, p = ctx.geo, ctx.palette
    body = [_rect(0, 0, g.sheet_w_mm, g.sheet_h_mm, p.ground)]

    for panel in g.panels:
        if panel.name == "front":
            # The front template works in its own canvas, which carries bleed on
            # all four edges. On the sheet the spine edge is a fold, not a trim,
            # so clip the group there to stop the front bleeding over the spine.
            # Bleed past the fore-edge only when no flap follows the front panel.
            has_flap = any(pnl.name == "front_flap" for pnl in g.panels)
            clip_w = panel.w_mm + (0.0 if has_flap else g.bleed_mm)
            clip = (
                f'<clipPath id="clip-front-panel"><rect x="{_n(panel.x_mm)}" y="0" '
                f'width="{_n(clip_w)}" height="{_n(g.sheet_h_mm)}"/></clipPath>'
            )
            # The clip lives on an outer group with no transform of its own: an
            # element's transform also applies to its clip-path, so combining the
            # two on one group would shift the clip out of the sheet.
            body.append(
                f'{clip}<g clip-path="url(#clip-front-panel)">'
                f'<g transform="translate({_n(panel.x_mm - g.bleed_mm)},0)">'
                f"{_rect(0, 0, g.front_bleed_w_mm, g.front_bleed_h_mm, p.ground)}"
                f"{FRONT_TEMPLATES[ctx.direction.template](ctx)}</g></g>"
            )
        elif panel.name == "back":
            body.append(_rect(panel.x_mm, panel.y_mm, panel.w_mm, panel.h_mm, p.ground))
            body.append(back_panel(ctx, panel.x_mm, panel.y_mm))
        elif panel.name == "spine":
            body.append(spine_panel(ctx, panel.x_mm, panel.y_mm))
        else:
            body.append(flap_panel(ctx, panel.x_mm, panel.y_mm, panel.w_mm, side=panel.name))

    if ctx.marks:
        body.append(print_marks(ctx))
    return _svg(g.sheet_w_mm, g.sheet_h_mm, "".join(body))
