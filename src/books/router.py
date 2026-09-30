"""Book endpoints.

    GET    /books           list, filter and search
    GET    /books/{slug}
    POST   /books
    PUT    /books/{slug}    full replace
    DELETE /books/{slug}
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from . import db
from .models import Book, BookList

router = APIRouter(prefix="/books", tags=["books"])

Conn = Annotated[sqlite3.Connection, Depends(db.get_db)]


def _conflict(exc: sqlite3.IntegrityError) -> HTTPException:
    field = "isbn" if "books.isbn" in str(exc) else "slug"
    return HTTPException(status.HTTP_409_CONFLICT, f"a book with this {field} already exists")


def _not_found(slug: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"no book {slug!r}")


@router.get("", response_model=BookList)
def list_books(
    conn: Conn,
    publisher: str | None = None,
    category: str | None = None,
    format: str | None = None,
    q: Annotated[str | None, Query(description="Search title, author and subtitle")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookList:
    items, total = db.list_books(
        conn, publisher=publisher, category=category, format=format,
        q=q, limit=limit, offset=offset,
    )
    return BookList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{slug}", response_model=Book)
def get_book(slug: str, conn: Conn) -> Book:
    book = db.get_book(conn, slug)
    if book is None:
        raise _not_found(slug)
    return book


@router.post("", response_model=Book, status_code=status.HTTP_201_CREATED)
def create_book(book: Book, conn: Conn) -> Book:
    try:
        with conn:
            db.insert(conn, book)
    except sqlite3.IntegrityError as exc:
        raise _conflict(exc) from exc
    return book


@router.put("/{slug}", response_model=Book)
def replace_book(slug: str, book: Book, conn: Conn) -> Book:
    try:
        with conn:
            found = db.replace(conn, slug, book)
    except sqlite3.IntegrityError as exc:
        raise _conflict(exc) from exc
    if not found:
        raise _not_found(slug)
    return book


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(slug: str, conn: Conn) -> Response:
    with conn:
        found = db.delete(conn, slug)
    if not found:
        raise _not_found(slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
