"""Book schemas, ported from recover/books.ts with snake_case fields."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

ReaderType = Literal["heart", "suspense", "trend", "discourse"]

Color = Annotated[str, Field(pattern=r"^#[0-9a-f]{6}$")]


class BookTheme(BaseModel):
    """Tailwind classes for a book page, sampled from its cover."""

    page: str = Field(description="Page background gradient")
    text: str
    soft: str
    button: str
    ring: str


class OriginalCover(BaseModel):
    url: str
    color: Color
    theme: BookTheme


class GeneratedCover(BaseModel):
    id: str
    type: ReaderType
    url: str
    color: Color
    theme: BookTheme


class Book(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=200)
    isbn: str = Field(pattern=r"^\d{13}$")
    title: str = Field(min_length=1)
    subtitle: str = ""
    author: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    url: str = Field(description="Official publisher page")
    category: str
    price: str = Field(description='As printed, e.g. "23,00 €"')
    pages: int = Field(gt=0)
    format: str
    blurb: str = ""
    original_cover: OriginalCover
    generated_covers: list[GeneratedCover] = []


class BookList(BaseModel):
    items: list[Book]
    total: int
    limit: int
    offset: int
