"""FastAPI app.

    POST /generate              a cover from a text prompt
    GET  /covers/{id}/{file}    the rendered assets
    GET  /catalogue             formats, templates, palettes, motifs
    GET  /healthz
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse

from .artdirection import MOTIFS, STYLES, TEMPLATES
from .formats import FORMATS, spine_mm
from .models import (
    CatalogueResponse,
    CoverRequest,
    CoverResponse,
    FormatInfo,
)
from .palettes import PALETTES
from .pipeline import create_cover
from .render import ASSETS, DEFAULT_ASSETS
from .typography import TYPE_FAMILIES

load_dotenv()
logging.basicConfig(level=os.environ.get("BOOKWORM_LOG_LEVEL", "INFO"))
log = logging.getLogger("bookworm")

OUTPUT_DIR = Path(os.environ.get("BOOKWORM_OUTPUT_DIR", "out")).resolve()

_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".json": "application/json",
}

app = FastAPI(
    title="bookworm",
    version="0.1.0",
    summary="Print-ready book covers in German trade formats, from a text prompt.",
    description=(
        "Claude writes the art direction, OpenAI paints the artwork, and the type is "
        "set as outlined vectors in real German trade geometry -- trim, bleed, spine "
        "and flaps included."
    ),
)


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
        "output_dir": str(OUTPUT_DIR),
    }


@app.get("/catalogue", response_model=CatalogueResponse)
def catalogue() -> CatalogueResponse:
    """Everything a caller can choose from."""
    return CatalogueResponse(
        formats=[
            FormatInfo(
                key=f.key,
                label=f.label,
                imprint=f.imprint,
                binding=f.binding,
                trim_mm=[f.trim_w_mm, f.trim_h_mm],
                flap_mm=f.flap_mm,
                spine_mm_at_288pp=spine_mm(f, 288),
            )
            for f in FORMATS.values()
        ],
        styles=list(STYLES),
        templates=list(TEMPLATES),
        type_families=list(TYPE_FAMILIES),
        palettes=[
            {
                "key": p.key,
                "label": p.label,
                "ground": p.ground,
                "ink": p.ink,
                "accent": p.accent,
                "secondary": p.secondary,
                "tone": list(p.tone),
            }
            for p in PALETTES
        ],
        motifs=list(MOTIFS),
        assets=list(ASSETS),
    )


@app.post("/generate", response_model=CoverResponse)
async def generate(
    request: CoverRequest,
    inline: str | None = Query(
        default=None,
        description=(
            "Return one asset directly instead of JSON, e.g. inline=front_png."
        ),
    ),
) -> Response | CoverResponse:
    if inline is not None and inline not in ASSETS:
        raise HTTPException(422, f"inline must be one of: {', '.join(ASSETS)}")

    assets = tuple(request.assets) if request.assets else DEFAULT_ASSETS
    if inline is not None and inline not in assets:
        assets = assets + (inline,)

    cover_id = uuid.uuid4().hex[:16]
    outdir = OUTPUT_DIR / cover_id

    try:
        result = await create_cover(
            text=request.text,
            title=request.title,
            author=request.author,
            outdir=outdir,
            format_key=request.format,
            pages=request.pages,
            dpi=request.dpi,
            imprint=request.imprint,
            isbn=request.isbn,
            price=request.price,
            translator=request.translator,
            template=request.template,
            type_family=request.type_family,
            palette=request.palette,
            artwork=request.artwork,
            motif=request.motif,
            genre_line=request.genre_line,
            blurb=request.blurb,
            style=request.style,
            treatment=request.treatment,
            seed=request.seed,
            marks=request.marks,
            spine_direction=request.spine_direction,
            assets=assets,
        )
    except KeyError as exc:
        raise HTTPException(422, str(exc.args[0] if exc.args else exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if inline is not None:
        path = Path(result["files"][inline])
        return FileResponse(
            path, media_type=_MEDIA.get(path.suffix, "application/octet-stream")
        )

    return CoverResponse(
        id=cover_id,
        assets={
            name: f"/covers/{cover_id}/{Path(p).name}"
            for name, p in result["files"].items()
        },
        art_direction=result["art_direction"],
        art_direction_meta=result["art_direction_meta"],
        artwork=result["artwork"],
        geometry=result["geometry"],
        content=result["content"],
        seed=result["seed"],
        notes=result["notes"],
    )


@app.get("/covers/{cover_id}/{filename}")
def asset(cover_id: str, filename: str) -> FileResponse:
    """Serve a rendered asset, refusing anything that escapes the output dir."""
    if not cover_id.isalnum():
        raise HTTPException(404, "not found")
    path = (OUTPUT_DIR / cover_id / filename).resolve()
    if not path.is_relative_to(OUTPUT_DIR) or not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(
        path, media_type=_MEDIA.get(path.suffix, "application/octet-stream")
    )
