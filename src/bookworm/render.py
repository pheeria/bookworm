"""Rasterise and write out the cover assets.

The SVG is the master: type is already outlined in it, so the PDF is vector and
carries no font dependency, and the PNG/JPEG are rendered from the exact same
geometry. All of this is CPU-bound and synchronous -- callers should push it to a
worker thread.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import _cairo
from .formats import px
from .layout import Ctx, build_front, build_spread

#: Asset names callers can ask for.
ASSETS = (
    "front_png",
    "front_jpg",
    "front_svg",
    "front_pdf",
    "spread_svg",
    "spread_pdf",
    "spread_png",
    "direction_json",
)
DEFAULT_ASSETS = ("front_png", "front_jpg", "front_svg", "spread_pdf", "direction_json")


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


def _write_raster(png: bytes, path: Path, dpi: int, *, quality: int = 95) -> None:
    """Write PNG or JPEG with the physical resolution stamped in the metadata.

    cairosvg emits no pHYs chunk, so a bare PNG would open at 72 dpi in a layout
    application even though the pixels are 300 dpi worth.
    """
    with Image.open(io.BytesIO(png)) as img:
        if path.suffix == ".jpg":
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
    """Build the requested assets into ``outdir``."""
    unknown = [a for a in assets if a not in ASSETS]
    if unknown:
        raise ValueError(
            f"unknown asset(s) {unknown}; available: {', '.join(ASSETS)}"
        )

    outdir.mkdir(parents=True, exist_ok=True)
    result = RenderResult()
    g = ctx.geo
    dpi = g.dpi

    need_front = any(a.startswith("front") for a in assets)
    need_spread = any(a.startswith("spread") for a in assets)

    front_svg = build_front(ctx) if need_front else ""
    # The spread is built second so notes from spine setting land in ctx.notes
    # before the caller reads them.
    spread_svg = build_spread(ctx) if need_spread else ""

    if "front_svg" in assets:
        p = outdir / "front.svg"
        p.write_text(front_svg, encoding="utf-8")
        result.files["front_svg"] = p

    if "front_png" in assets or "front_jpg" in assets:
        w = px(g.front_bleed_w_mm, dpi)
        h = px(g.front_bleed_h_mm, dpi)
        png = _png_bytes(front_svg, w, h)
        if "front_png" in assets:
            p = outdir / "front.png"
            _write_raster(png, p, dpi)
            result.files["front_png"] = p
        if "front_jpg" in assets:
            p = outdir / "front.jpg"
            _write_raster(png, p, dpi)
            result.files["front_jpg"] = p

    if "front_pdf" in assets:
        p = outdir / "front.pdf"
        _cairo.svg2pdf(bytestring=front_svg.encode("utf-8"), write_to=str(p))
        result.files["front_pdf"] = p

    if "spread_svg" in assets:
        p = outdir / "spread.svg"
        p.write_text(spread_svg, encoding="utf-8")
        result.files["spread_svg"] = p

    if "spread_png" in assets:
        p = outdir / "spread.png"
        _write_raster(
            _png_bytes(spread_svg, px(g.sheet_w_mm, dpi), px(g.sheet_h_mm, dpi)),
            p,
            dpi,
        )
        result.files["spread_png"] = p

    if "spread_pdf" in assets:
        p = outdir / "spread.pdf"
        _cairo.svg2pdf(bytestring=spread_svg.encode("utf-8"), write_to=str(p))
        result.files["spread_pdf"] = p

    if "direction_json" in assets:
        p = outdir / "direction.json"
        p.write_text(
            json.dumps(
                {
                    "art_direction": ctx.direction.model_dump(),
                    "content": {
                        "title": ctx.content.title,
                        "author": ctx.content.author,
                        "genre_line": ctx.content.genre_line,
                        "blurb": ctx.content.blurb,
                        "imprint": ctx.content.imprint,
                    },
                    "geometry": g.to_dict(),
                    "seed": ctx.seed,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result.files["direction_json"] = p

    result.notes = list(ctx.notes)
    return result
