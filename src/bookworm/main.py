"""The bookworm API: books and covers in one app.

    uv run uvicorn bookworm.main:app --reload

This module owns the process: it loads ``.env``, configures CORS and connects
the books API to MongoDB. ``books`` and ``covers`` stay libraries.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Before importing covers: it reads its settings at import time.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

import books
from books import db as books_db
from covers.main import app as covers_app
from covers.main import cors_origins


@asynccontextmanager
async def lifespan(_: FastAPI):
    client = books_db.connect()
    books_db.init(client)
    yield
    client.close()


app = FastAPI(
    title="bookworm",
    version="0.1.0",
    summary="Book metadata and print-ready covers for German trade books.",
    lifespan=lifespan,
)

app.include_router(books.router)
# Take the covers endpoints but not the covers app's own /docs and /openapi.json.
app.router.routes.extend(r for r in covers_app.routes if isinstance(r, APIRoute))


# covers' origin policy, plus the methods the books API writes with.
app.add_middleware(
    CORSMiddleware,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
    **cors_origins(),
)
