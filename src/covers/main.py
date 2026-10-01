"""The covers API as a standalone app.

    uv run uvicorn covers.main:app

This module configures the process -- logging and CORS -- so the rest of the
package does not have to. ``bookworm`` includes :data:`covers.api.router`
instead and never imports this.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import settings
from .api import cors_origins, router
from .imagethread import tune_allocator
from .typography import check_fonts


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail to boot rather than after an image has been paid for.
    await asyncio.to_thread(check_fonts)
    yield


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.log_level())
    tune_allocator()
    app = FastAPI(
        lifespan=lifespan,
        title="covers",
        version="0.1.0",
        summary="Print-ready front covers in German trade formats, from a text prompt.",
        description=(
            "Claude writes the art direction, an image model paints the artwork, and the type is "
            "set as outlined vectors in real German trade geometry, trim and bleed included."
        ),
    )
    app.include_router(router)
    app.add_middleware(
        CORSMiddleware,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        **cors_origins(),
    )
    return app


app = create_app()
