"""Book endpoints.

    GET    /books           list, filter and search; cacheable for 5 minutes
    GET    /books/{slug}
    POST   /books
    PUT    /books/{slug}    full replace, except generated_covers
    DELETE /books/{slug}
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from . import db
from .models import Book, BookList

router = APIRouter(prefix="/books", tags=["books"])

Books = Annotated[Collection, Depends(db.get_db)]

#: How long a client or CDN may reuse the book list: an edit shows within this.
LIST_MAX_AGE = 5 * 60


def _conflict(exc: DuplicateKeyError) -> HTTPException:
    field = "isbn" if "isbn" in (exc.details or {}).get("keyPattern", {}) else "slug"
    return HTTPException(status.HTTP_409_CONFLICT, f"a book with this {field} already exists")


def not_found(slug: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"no book {slug!r}")


@router.get("", response_model=BookList)
def list_books(
    books: Books,
    response: Response,
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
    response.headers["Cache-Control"] = f"public, max-age={LIST_MAX_AGE}"
    return BookList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{slug}", response_model=Book)
def get_book(slug: str, books: Books) -> Book:
    book = db.get_book(books, slug)
    if book is None:
        raise not_found(slug)
    return book


@router.post("", response_model=Book, status_code=status.HTTP_201_CREATED)
def create_book(book: Book, books: Books) -> Book:
    try:
        return db.insert(books, book)
    except DuplicateKeyError as exc:
        raise _conflict(exc) from exc


@router.put("/{slug}", response_model=Book)
def replace_book(slug: str, book: Book, books: Books) -> Book:
    try:
        stored = db.replace(books, slug, book)
    except DuplicateKeyError as exc:
        raise _conflict(exc) from exc
    if stored is None:
        raise not_found(slug)
    return stored


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(slug: str, books: Books) -> None:
    found = db.delete(books, slug)
    if not found:
        raise not_found(slug)
