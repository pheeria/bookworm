"""Rasterise the front cover to PNG.

The SVG is the master: type is already outlined in it, and the PNG is rendered
from that exact geometry. This is CPU-bound and synchronous -- callers should
push it to a worker thread.
"""

import io
import struct
import zlib

from . import _cairo
from .formats import MM_PER_INCH, px
from .layout import ARTWORK_HREF, Ctx, build_front

FILENAME = "front.png"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR_END = len(_PNG_SIGNATURE) + 4 + 4 + 13 + 4  # length, type, data, CRC


def render(ctx: Ctx) -> bytes:
    """The front cover as PNG bytes, with its physical resolution stamped in."""
    g = ctx.geo
    resources = {}
    if ctx.artwork_image is not None:
        # PNG, lightly compressed: cairo reads it directly, where any other format
        # is decoded and re-encoded as PNG first.
        buf = io.BytesIO()
        ctx.artwork_image.save(buf, format="PNG", compress_level=1)
        resources[ARTWORK_HREF] = buf.getvalue()
    png = _cairo.svg2png(
        build_front(ctx).encode("utf-8"),
        output_width=px(g.front_bleed_w_mm, g.dpi),
        output_height=px(g.front_bleed_h_mm, g.dpi),
        resources=resources,
    )
    return with_dpi(png, g.dpi)


def with_dpi(png: bytes, dpi: int) -> bytes:
    """Insert a pHYs chunk after IHDR.

    cairosvg emits none, so a bare PNG would open at 72 dpi in a layout
    application even though the pixels are 300 dpi worth. Splicing the chunk in
    avoids decoding and re-compressing the whole image to set two numbers.
    """
    ppm = round(dpi / MM_PER_INCH * 1000)  # pixels per metre
    body = b"pHYs" + struct.pack(">IIB", ppm, ppm, 1)
    chunk = struct.pack(">I", 9) + body + struct.pack(">I", zlib.crc32(body))
    return png[:_IHDR_END] + chunk + png[_IHDR_END:]
