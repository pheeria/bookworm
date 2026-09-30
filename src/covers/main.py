"""FastAPI app.

    POST /generate              a front cover from a text prompt
    GET  /covers/{id}/front.png the rendered cover
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
from .formats import FORMATS
from .models import (
    CatalogueResponse,
    CoverRequest,
    CoverResponse,
    FormatInfo,
)
from .palettes import PALETTES
from .pipeline import create_cover
from .render import FILENAME
from .typography import TYPE_FAMILIES

logging.basicConfig(level=os.environ.get("COVERS_LOG_LEVEL", "INFO"))

OUTPUT_DIR = Path(os.environ.get("COVERS_OUTPUT_DIR", "out")).resolve()

def _file(path: Path) -> FileResponse:
    return FileResponse(path, media_type="image/png")

app = FastAPI(
    title="bookworm",
    version="0.1.0",
    summary="Print-ready front covers in German trade formats, from a text prompt.",
    description=(
        "Claude writes the art direction, OpenAI paints the artwork, and the type is "
        "set as outlined vectors in real German trade geometry, trim and bleed included."
    ),
)


#: Browser origins allowed to call the API.
#:
#: The default is any localhost port, which covers a dev front end without
#: opening the service to the web at large. That matters more here than for a
#: read-only API: `POST /generate` spends real money on image generation, so a
#: wildcard would let any page you happen to visit bill your OpenAI account.
#: Set COVERS_CORS_ORIGINS to a comma-separated list, or "*" to allow all.
_LOCALHOST = r"http://(localhost|127\.0\.0\.1)(:\d+)?"


def cors_origins() -> dict:
    """CORSMiddleware origin kwargs for this policy; callers choose the methods."""
    origins = os.environ.get("COVERS_CORS_ORIGINS", "").strip()
    if origins == "*":
        return {"allow_origins": ["*"]}
    if origins:
        return {"allow_origins": [o.strip() for o in origins.split(",") if o.strip()]}
    return {"allow_origin_regex": _LOCALHOST}


app.add_middleware(
    CORSMiddleware,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    **cors_origins(),
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
            )
            for f in FORMATS.values()
        ],
        styles=list(STYLES),
        templates=list(TEMPLATES),
        type_families=list(TYPE_FAMILIES),
        palettes=[asdict(p) for p in PALETTES],
        motifs=list(MOTIFS),
    )


@app.post("/generate", response_model=CoverResponse)
async def generate(
    request: CoverRequest,
    inline: bool = Query(default=False, description="Return the PNG instead of JSON."),
) -> Response | CoverResponse:
    cover_id = uuid.uuid4().hex[:16]
    try:
        result = await create_cover(
            **request.model_dump(exclude={"format"}),
            format_key=request.format,
            outdir=OUTPUT_DIR / cover_id,
        )
    except KeyError as exc:
        raise HTTPException(422, str(exc.args[0] if exc.args else exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if inline:
        return _file(Path(result["image"]))

    return CoverResponse(
        id=cover_id,
        image=f"/covers/{cover_id}/{FILENAME}",
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


@app.get(f"/covers/{{cover_id}}/{FILENAME}")
def image(cover_id: str) -> FileResponse:
    """Serve a rendered cover."""
    path = OUTPUT_DIR / cover_id / FILENAME
    if not cover_id.isalnum() or not path.is_file():
        raise HTTPException(404, "not found")
    return _file(path)
