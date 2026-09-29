"""Abstract vector motifs, in the flat constructivist register the idiom allows.

Each motif draws into a box in millimetre coordinates and returns an SVG fragment.
They are seeded, so the same request always yields the same cover.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from .palettes import Palette
from .svg import n as _n
from .svg import rect as _rect


def arcs(x: float, y: float, w: float, h: float, p: Palette, rng: random.Random) -> str:
    """Concentric half-circles rising from the baseline of the box."""
    cx, base = x + w / 2, y + h
    rings = rng.randint(4, 7)
    step = min(w / 2, h) / rings
    out = []
    for i in range(rings, 0, -1):
        r = step * i
        fill = p.accent if i % 2 else p.secondary
        out.append(
            f'<path d="M {_n(cx - r)} {_n(base)} A {_n(r)} {_n(r)} 0 0 1 '
            f'{_n(cx + r)} {_n(base)} Z" fill="{fill}"/>'
        )
    return "".join(out)


def blocks(x: float, y: float, w: float, h: float, p: Palette, rng: random.Random) -> str:
    """Rectangles on a coarse grid -- a few, never busy."""
    cols, rows = rng.choice([(3, 4), (4, 5), (2, 3)])
    cw, ch = w / cols, h / rows
    cells = [(c, r) for c in range(cols) for r in range(rows)]
    rng.shuffle(cells)
    out = []
    for i, (c, r) in enumerate(cells[: rng.randint(3, 6)]):
        fill = p.accent if i % 2 else p.secondary
        out.append(_rect(x + c * cw, y + r * ch, cw, ch, fill))
    return "".join(out)


def dots(x: float, y: float, w: float, h: float, p: Palette, rng: random.Random) -> str:
    """A dot grid that thins out towards one edge."""
    cols = rng.randint(6, 10)
    step = w / cols
    rows = max(1, int(h / step))
    out = []
    for r in range(rows):
        for c in range(cols):
            t = (r + 1) / rows
            radius = step * 0.42 * t
            if radius < step * 0.06:
                continue
            fill = p.accent if (r + c) % 3 else p.secondary
            out.append(
                f'<circle cx="{_n(x + (c + 0.5) * step)}" '
                f'cy="{_n(y + (r + 0.5) * step)}" r="{_n(radius)}" fill="{fill}"/>'
            )
    return "".join(out)


def split(x: float, y: float, w: float, h: float, p: Palette, rng: random.Random) -> str:
    """One diagonal cut across the box."""
    top = rng.uniform(0.15, 0.55)
    bottom = rng.uniform(0.45, 0.9)
    return (
        f'<path d="M {_n(x)} {_n(y + h * top)} L {_n(x + w)} {_n(y + h * bottom)} '
        f'L {_n(x + w)} {_n(y + h)} L {_n(x)} {_n(y + h)} Z" fill="{p.accent}"/>'
        f'<path d="M {_n(x)} {_n(y + h * top)} L {_n(x + w)} {_n(y + h * bottom)} '
        f'L {_n(x + w)} {_n(y + h * bottom + h * 0.035)} '
        f'L {_n(x)} {_n(y + h * top + h * 0.035)} Z" fill="{p.secondary}"/>'
    )


def waveform(x: float, y: float, w: float, h: float, p: Palette, rng: random.Random) -> str:
    """Vertical bars of varying height, read as tension or signal."""
    n = rng.randint(14, 26)
    gap = w / n
    bar = gap * 0.55
    out = []
    for i in range(n):
        t = i / max(1, n - 1)
        amp = (0.25 + 0.75 * abs(rng.gauss(0.5, 0.28))) * (0.4 + 0.6 * (1 - abs(t - 0.5) * 2))
        bh = max(h * 0.04, min(h, h * amp))
        out.append(
            _rect(
                x + i * gap + (gap - bar) / 2, y + h - bh, bar, bh,
                p.accent if i % 4 else p.secondary,
            )
        )
    return "".join(out)


def rings(x: float, y: float, w: float, h: float, p: Palette, rng: random.Random) -> str:
    """Offset concentric outlines, like a target gone slightly wrong."""
    cx, cy = x + w / 2, y + h / 2
    n = rng.randint(5, 9)
    r_max = min(w, h) / 2
    stroke = r_max / (n * 3.2)
    drift = r_max * 0.06
    out = []
    for i in range(n, 0, -1):
        r = r_max * i / n
        ox = rng.uniform(-drift, drift)
        oy = rng.uniform(-drift, drift)
        out.append(
            f'<circle cx="{_n(cx + ox)}" cy="{_n(cy + oy)}" r="{_n(max(r - stroke, 0.1))}" '
            f'fill="none" stroke="{p.accent if i % 2 else p.secondary}" '
            f'stroke-width="{_n(stroke)}"/>'
        )
    return "".join(out)


def none_(*_args, **_kwargs) -> str:
    return ""


MOTIFS: dict[str, Callable[..., str]] = {
    "arcs": arcs,
    "blocks": blocks,
    "dots": dots,
    "split": split,
    "waveform": waveform,
    "rings": rings,
    "none": none_,
}


CLIP_ID = "clip-front-art"


def draw(
    name: str, x: float, y: float, w: float, h: float, palette: Palette, seed: int
) -> str:
    """Draw ``name`` clipped to the box."""
    body = MOTIFS.get(name, none_)(x, y, w, h, palette, random.Random(seed))
    if not body:
        return ""
    return (
        f'<clipPath id="{CLIP_ID}">'
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"/>'
        f"</clipPath>"
        f'<g clip-path="url(#{CLIP_ID})">{body}</g>'
    )
