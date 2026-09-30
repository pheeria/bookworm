"""The covers endpoints, as a router any app can include.

    POST /generate              a front cover from a text prompt
    GET  /covers/{id}/front.png the rendered cover
    GET  /catalogue             formats, templates, palettes, motifs
    GET  /healthz

Nothing here configures the process; :mod:`covers.main` wraps this in a
standalone app, and ``bookworm`` includes it in its own.
"""

import os
import uuid
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from . import settings
from .artdirection import MOTIFS, STYLES, TEMPLATES
from .formats import FORMATS
from .models import CatalogueResponse, CoverRequest, CoverResponse, FormatInfo
from .palettes import PALETTES
from .pipeline import create_cover
from .render import FILENAME
from .typography import TYPE_FAMILIES

router = APIRouter(tags=["covers"])

#: The default browser-origin policy is any localhost port, which covers a dev
#: front end without opening the service to the web at large. That matters more
#: here than for a read-only API: `POST /generate` spends real money on image
#: generation, so a wildcard would let any page you happen to visit bill your
#: OpenAI account. Set COVERS_CORS_ORIGINS to a comma-separated list, or "*".
_LOCALHOST = r"http://(localhost|127\.0\.0\.1)(:\d+)?"


def cors_origins() -> dict:
    """CORSMiddleware origin kwargs for this policy; apps choose the methods."""
    origins = os.environ.get("COVERS_CORS_ORIGINS", "").strip()
    if origins == "*":
        return {"allow_origins": ["*"]}
    if origins:
        return {"allow_origins": [o.strip() for o in origins.split(",") if o.strip()]}
    return {"allow_origin_regex": _LOCALHOST}


@router.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
        "output_dir": str(settings.output_dir()),
    }


@router.get("/catalogue", response_model=CatalogueResponse)
def catalogue() -> CatalogueResponse:
    """Everything a caller can choose from."""
    return CatalogueResponse(
        formats=[FormatInfo(**f.summary()) for f in FORMATS.values()],
        styles=list(STYLES),
        templates=list(TEMPLATES),
        type_families=list(TYPE_FAMILIES),
        palettes=[asdict(p) for p in PALETTES],
        motifs=list(MOTIFS),
    )


@router.post("/generate", response_model=CoverResponse)
async def generate(
    request: CoverRequest,
    inline: bool = Query(default=False, description="Return the PNG instead of JSON."),
) -> Response | CoverResponse:
    result = await create_cover(**request.model_dump())
    if inline:
        return Response(result["png"], media_type="image/png")

    cover_id = uuid.uuid4().hex[:16]
    path = settings.output_dir() / cover_id / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result["png"])
    return CoverResponse.model_validate(
        {**result, "id": cover_id, "image": f"/covers/{cover_id}/{FILENAME}"}
    )


@router.get(f"/covers/{{cover_id}}/{FILENAME}")
def image(cover_id: str) -> FileResponse:
    """Serve a cover ``/generate`` rendered."""
    path = settings.output_dir() / cover_id / FILENAME
    if not cover_id.isalnum() or not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="image/png")
