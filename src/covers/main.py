"""FastAPI app.

    POST /generate              a cover from a text prompt
    GET  /covers/{id}/{file}    the rendered assets
    GET  /catalogue             formats, templates, palettes, motifs
    GET  /healthz
"""

import logging
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
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
from .assets import ASSET_NAMES, DEFAULT_ASSETS, MEDIA_BY_SUFFIX
from .typography import TYPE_FAMILIES

logging.basicConfig(level=os.environ.get("COVERS_LOG_LEVEL", "INFO"))

OUTPUT_DIR = Path(os.environ.get("COVERS_OUTPUT_DIR", "out")).resolve()

def _file(path: Path) -> FileResponse:
    media = MEDIA_BY_SUFFIX.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media)

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


#: Browser origins allowed to call the API.
#:
#: The default is any localhost port, which covers a dev front end without
#: opening the service to the web at large. That matters more here than for a
#: read-only API: `POST /generate` spends real money on image generation, so a
#: wildcard would let any page you happen to visit bill your OpenAI account.
#: Set COVERS_CORS_ORIGINS to a comma-separated list, or "*" to allow all.
_CORS_ORIGINS = os.environ.get("COVERS_CORS_ORIGINS", "").strip()
_LOCALHOST = r"http://(localhost|127\.0\.0\.1)(:\d+)?"

if _CORS_ORIGINS == "*":
    _cors = {"allow_origins": ["*"]}
elif _CORS_ORIGINS:
    _cors = {"allow_origins": [o.strip() for o in _CORS_ORIGINS.split(",") if o.strip()]}
else:
    _cors = {"allow_origin_regex": _LOCALHOST}

app.add_middleware(
    CORSMiddleware,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    **_cors,
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
        palettes=[asdict(p) for p in PALETTES],
        motifs=list(MOTIFS),
        assets=list(ASSET_NAMES),
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
    assets = tuple(request.assets) if request.assets else DEFAULT_ASSETS
    if inline is not None:
        if inline not in ASSET_NAMES:
            raise HTTPException(422, f"inline must be one of: {', '.join(ASSET_NAMES)}")
        if inline not in assets:
            assets += (inline,)

    cover_id = uuid.uuid4().hex[:16]
    outdir = OUTPUT_DIR / cover_id

    try:
        result = await create_cover(
            **request.model_dump(exclude={"format", "assets"}),
            format_key=request.format,
            outdir=outdir,
            assets=assets,
        )
    except KeyError as exc:
        raise HTTPException(422, str(exc.args[0] if exc.args else exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if inline is not None:
        return _file(Path(result["files"][inline]))

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
        suggestions=result["suggestions"],
        style=result["style"],
        director=result["director"],
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
    return _file(path)
