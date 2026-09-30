"""The bookworm API: books and covers in one app.

    uv run uvicorn bookworm.main:app --reload

This module owns the process: it loads ``.env``, configures CORS and opens the
books database. ``books`` and ``covers`` stay libraries.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Before importing covers: it reads its settings at import time.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

import books  # noqa: E402
from books import db as books_db  # noqa: E402
from covers.main import app as covers_app  # noqa: E402


@asynccontextmanager
async def lifespan(_: FastAPI):
    books_db.init()
    yield


app = FastAPI(
    title="bookworm",
    version="0.1.0",
    summary="Book metadata and print-ready covers for German trade books.",
    lifespan=lifespan,
)

app.include_router(books.router)
# Take the covers endpoints but not the covers app's own /docs and /openapi.json.
app.router.routes.extend(r for r in covers_app.routes if isinstance(r, APIRoute))


#: Same policy as covers.main: any localhost port unless COVERS_CORS_ORIGINS says
#: otherwise. Not a wildcard: POST /generate spends money and the books API writes.
_CORS_ORIGINS = os.environ.get("COVERS_CORS_ORIGINS", "").strip()
if _CORS_ORIGINS == "*":
    _cors = {"allow_origins": ["*"]}
elif _CORS_ORIGINS:
    _cors = {"allow_origins": [o.strip() for o in _CORS_ORIGINS.split(",") if o.strip()]}
else:
    _cors = {"allow_origin_regex": r"http://(localhost|127\.0\.0\.1)(:\d+)?"}

app.add_middleware(
    CORSMiddleware,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
    **_cors,
)
