"""Rasterise and write out the cover assets.

The SVG is the master: type is already outlined in it, so the PDF is vector and
carries no font dependency, and the PNG/JPEG are rendered from the exact same
geometry. All of this is CPU-bound and synchronous -- callers should push it to a
worker thread.
"""

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import _cairo
from .assets import ASSETS, DEFAULT_ASSETS, needs
from .formats import px
from .layout import Ctx, build_front, build_spread


@dataclass
class RenderResult:
    files: dict[str, Path] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _png_bytes(svg: str, w_px: int, h_px: int) -> bytes:
    return _cairo.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=w_px,
        output_height=h_px,
    )


def _write_raster(png: bytes, path: Path, fmt: str, dpi: int, *, quality: int = 95) -> None:
    """Write PNG or JPEG with the physical resolution stamped in the metadata.

    cairosvg emits no pHYs chunk, so a bare PNG would open at 72 dpi in a layout
    application even though the pixels are 300 dpi worth.
    """
    with Image.open(io.BytesIO(png)) as img:
        if fmt == "jpeg":
            img.convert("RGB").save(
                path,
                format="JPEG",
                quality=quality,
                subsampling=1,
                optimize=True,
                dpi=(dpi, dpi),
            )
        else:
            img.save(path, format="PNG", dpi=(dpi, dpi))


def render(ctx: Ctx, outdir: Path, assets: tuple[str, ...] = DEFAULT_ASSETS) -> RenderResult:
    unknown = [a for a in assets if a not in ASSETS]
    if unknown:
        raise ValueError(
            f"unknown asset(s) {unknown}; available: {', '.join(ASSETS)}"
        )

    outdir.mkdir(parents=True, exist_ok=True)
    result = RenderResult()
    g = ctx.geo
    dpi = g.dpi

    front_svg = build_front(ctx) if needs(assets, "front") else ""
    # The spread is built second so notes from spine setting land in ctx.notes
    # before the caller reads them.
    spread_svg = build_spread(ctx) if needs(assets, "spread") else ""
    svg = {"front": front_svg, "spread": spread_svg}
    size = {
        "front": (px(g.front_bleed_w_mm, dpi), px(g.front_bleed_h_mm, dpi)),
        "spread": (px(g.sheet_w_mm, dpi), px(g.sheet_h_mm, dpi)),
    }

    # One rasterisation per surface, shared by its PNG and JPEG.
    raster: dict[str, bytes] = {}
    for surface in ("front", "spread"):
        if any(
            ASSETS[a].surface == surface and ASSETS[a].format in ("png", "jpeg")
            for a in assets
        ):
            raster[surface] = _png_bytes(svg[surface], *size[surface])

    for name in assets:
        asset = ASSETS[name]
        path = outdir / asset.filename
        if asset.format in ("png", "jpeg"):
            _write_raster(raster[asset.surface], path, asset.format, dpi)
        elif asset.format == "svg":
            path.write_text(svg[asset.surface], encoding="utf-8")
        elif asset.format == "pdf":
            _cairo.svg2pdf(bytestring=svg[asset.surface].encode("utf-8"), write_to=str(path))
        else:
            path.write_text(
                json.dumps(
                    {
                        "art_direction": ctx.direction.model_dump(),
                        "content": ctx.content.summary(),
                        "geometry": g.to_dict(),
                        "seed": ctx.seed,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        result.files[name] = path

    result.notes = list(ctx.notes)
    return result
