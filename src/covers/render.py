"""Rasterise the front cover to PNG.

The SVG is the master: type is already outlined in it, and the PNG is rendered
from that exact geometry. This is CPU-bound and synchronous -- callers should
push it to a worker thread.
"""

import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import _cairo
from .formats import px
from .layout import Ctx, build_front

FILENAME = "front.png"


@dataclass
class RenderResult:
    image: Path
    notes: list[str] = field(default_factory=list)


def render(ctx: Ctx, outdir: Path) -> RenderResult:
    """Write ``front.png`` into ``outdir``, with the physical resolution stamped in.

    cairosvg emits no pHYs chunk, so a bare PNG would open at 72 dpi in a layout
    application even though the pixels are 300 dpi worth.
    """
    g = ctx.geo
    png = _cairo.svg2png(
        bytestring=build_front(ctx).encode("utf-8"),
        output_width=px(g.front_bleed_w_mm, g.dpi),
        output_height=px(g.front_bleed_h_mm, g.dpi),
    )
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / FILENAME
    with Image.open(io.BytesIO(png)) as img:
        img.save(path, format="PNG", dpi=(g.dpi, g.dpi))
    return RenderResult(image=path, notes=list(ctx.notes))
