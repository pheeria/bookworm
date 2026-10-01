"""The bookworm API: books and covers in one app.

    uv run uvicorn bookworm.main:app --reload

This module owns the process: it loads ``.env``, configures logging and CORS, and
connects the books API to MongoDB -- but only for the served ``app``, which is
built on first access. Importing the module, or calling :func:`create_app` as the
tests do, reads no ``.env`` and so cannot pick up real provider keys. ``books`` and ``covers`` stay libraries; the covers made
for books (:mod:`bookworm.book_covers`) are the one place they meet.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

import books
import covers.api
from books import db as books_db
from covers import settings as covers_settings
from covers.imagethread import tune_allocator
from covers.typography import check_fonts

from . import book_covers, cover_store


def create_app(client_factory: Callable[[], MongoClient] = books_db.connect) -> FastAPI:
    """The API, with books stored through ``client_factory()`` (tests pass mongomock)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Every typeface loads now, or the app does not boot: a missing face must
        # not surface mid-render, after an image has been paid for.
        await asyncio.to_thread(check_fonts)
        client = client_factory()
        app.state.books = books_db.init(client)
        app.state.covers, app.state.cores, app.state.cover_images = cover_store.init(
            app.state.books.database
        )
        yield
        client.close()

    app = FastAPI(
        title="bookworm",
        version="0.1.0",
        summary="Book metadata and print-ready covers for German trade books.",
        lifespan=lifespan,
    )

    app.include_router(books.router)
    app.include_router(book_covers.router)
    app.include_router(covers.api.router)

    # covers' origin policy, plus the methods the books API writes with.
    app.add_middleware(
        CORSMiddleware,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        **covers.api.cors_origins(),
    )
    return app


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_served: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """``bookworm.main:app``, built when uvicorn first asks for it."""
    global _served
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if _served is None:
        load_dotenv(_ENV_FILE)
        logging.basicConfig(level=covers_settings.log_level())
        tune_allocator()
        _served = create_app()
    return _served
