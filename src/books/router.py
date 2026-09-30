"""Book endpoints.

    GET    /books           list, filter and search
    GET    /books/{slug}
    POST   /books
    PUT    /books/{slug}    full replace
    DELETE /books/{slug}
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from . import db
from .models import Book, BookList

router = APIRouter(prefix="/books", tags=["books"])

Books = Annotated[Collection, Depends(db.get_db)]


def _conflict(exc: DuplicateKeyError) -> HTTPException:
    key = (exc.details or {}).get("keyPattern") or {}
    field = "isbn" if "isbn" in key or "isbn_1" in str(exc) else "slug"
    return HTTPException(status.HTTP_409_CONFLICT, f"a book with this {field} already exists")


def _not_found(slug: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"no book {slug!r}")


@router.get("", response_model=BookList)
def list_books(
    books: Books,
    publisher: str | None = None,
    category: str | None = None,
    format: str | None = None,
    q: Annotated[str | None, Query(description="Search title, author and subtitle")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookList:
    items, total = db.list_books(
        books, publisher=publisher, category=category, format=format,
        q=q, limit=limit, offset=offset,
    )
    return BookList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{slug}", response_model=Book)
def get_book(slug: str, books: Books) -> Book:
    book = db.get_book(books, slug)
    if book is None:
        raise _not_found(slug)
    return book


@router.post("", response_model=Book, status_code=status.HTTP_201_CREATED)
def create_book(book: Book, books: Books) -> Book:
    try:
        db.insert(books, book)
    except DuplicateKeyError as exc:
        raise _conflict(exc) from exc
    return book


@router.put("/{slug}", response_model=Book)
def replace_book(slug: str, book: Book, books: Books) -> Book:
    try:
        found = db.replace(books, slug, book)
    except DuplicateKeyError as exc:
        raise _conflict(exc) from exc
    if not found:
        raise _not_found(slug)
    return book


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(slug: str, books: Books) -> None:
    found = db.delete(books, slug)
    if not found:
        raise _not_found(slug)
