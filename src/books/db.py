"""SQLite storage for books.

Covers are stored as JSON columns: nothing queries into them, and they are only
ever read and written together with their book.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .models import Book

SEED = Path(__file__).with_name("seed.json")

_COLUMNS = tuple(Book.model_fields)
_JSON = ("original_cover", "generated_covers")

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    slug             TEXT PRIMARY KEY,
    isbn             TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    subtitle         TEXT NOT NULL,
    author           TEXT NOT NULL,
    publisher        TEXT NOT NULL,
    url              TEXT NOT NULL,
    category         TEXT NOT NULL,
    price            TEXT NOT NULL,
    pages            INTEGER NOT NULL,
    format           TEXT NOT NULL,
    blurb            TEXT NOT NULL,
    original_cover   TEXT NOT NULL,
    generated_covers TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS books_publisher ON books(publisher);
CREATE INDEX IF NOT EXISTS books_category  ON books(category);
CREATE INDEX IF NOT EXISTS books_format    ON books(format);
"""


def db_path() -> Path:
    return Path(os.environ.get("BOOKS_DB_PATH", "data/books.db")).resolve()


def connect(path: Path | None = None) -> sqlite3.Connection:
    # FastAPI runs sync dependencies and endpoints on different threadpool threads.
    conn = sqlite3.connect(path or db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # LIKE is case-insensitive for ASCII only, so search folds in Python for umlauts.
    conn.create_function("fold", 1, str.casefold, deterministic=True)
    return conn


def init(path: Path | None = None) -> None:
    """Create the schema, and seed from seed.json if the table is empty."""
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        with conn:
            conn.executescript(SCHEMA)
            if conn.execute("SELECT 1 FROM books LIMIT 1").fetchone() is None:
                for raw in json.loads(SEED.read_text(encoding="utf-8")):
                    insert(conn, Book.model_validate(raw))
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _values(book: Book) -> dict:
    data = book.model_dump()
    for key in _JSON:
        data[key] = json.dumps(data[key], ensure_ascii=False)
    return data


def _book(row: sqlite3.Row) -> Book:
    data = dict(row)  # created_at/updated_at are ignored by the model
    for key in _JSON:
        data[key] = json.loads(row[key])
    return Book.model_validate(data)


def list_books(
    conn: sqlite3.Connection,
    *,
    publisher: str | None = None,
    category: str | None = None,
    format: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Book], int]:
    clauses, params = [], []
    for column, value in (("publisher", publisher), ("category", category), ("format", format)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if q:
        clauses.append("(fold(title) LIKE ? OR fold(author) LIKE ? OR fold(subtitle) LIKE ?)")
        like = f"%{q.casefold()}%"
        params += [like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = conn.execute(f"SELECT COUNT(*) FROM books {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM books {where} ORDER BY rowid LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [_book(r) for r in rows], total


def get_book(conn: sqlite3.Connection, slug: str) -> Book | None:
    row = conn.execute("SELECT * FROM books WHERE slug = ?", (slug,)).fetchone()
    return _book(row) if row else None


def insert(conn: sqlite3.Connection, book: Book) -> None:
    """Raises sqlite3.IntegrityError on a duplicate slug or ISBN."""
    now = _now()
    columns = (*_COLUMNS, "created_at", "updated_at")
    conn.execute(
        f"INSERT INTO books ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)})",
        {**_values(book), "created_at": now, "updated_at": now},
    )


def replace(conn: sqlite3.Connection, slug: str, book: Book) -> bool:
    """Replace the book at ``slug``. False if there is none; IntegrityError on a clash."""
    assignments = ", ".join(f"{c} = :{c}" for c in (*_COLUMNS, "updated_at"))
    cursor = conn.execute(
        f"UPDATE books SET {assignments} WHERE slug = :old_slug",
        {**_values(book), "updated_at": _now(), "old_slug": slug},
    )
    return cursor.rowcount > 0


def delete(conn: sqlite3.Connection, slug: str) -> bool:
    return conn.execute("DELETE FROM books WHERE slug = ?", (slug,)).rowcount > 0
