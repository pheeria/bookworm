"""The books CRUD API, against an in-memory mongomock database per test."""

import copy
import json

import mongomock
import pytest
from fastapi.testclient import TestClient

from books import db
from books.db import SEED
from books.models import GeneratedCover
from bookworm.main import create_app

SEEDED = json.loads(SEED.read_text(encoding="utf-8"))


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(mongomock.MongoClient)) as c:  # lifespan seeds it
        yield c


def new_book(**overrides) -> dict:
    book = copy.deepcopy(SEEDED[0])
    book.update(slug="helena-marr-die-zweite-sprache-9783462001234",
                isbn="9783462001234", title="Die zweite Sprache", author="Helena Marr")
    book.update(overrides)
    return book


def test_seeded_from_books_ts(client):
    body = client.get("/books", params={"limit": 200}).json()
    assert body["total"] == len(SEEDED) == 25
    assert [b["slug"] for b in body["items"]] == [b["slug"] for b in SEEDED]


def test_seeding_is_idempotent(client):
    from books import db

    db.init(client.app.state.books.database.client)
    assert client.get("/books").json()["total"] == 25


def test_filters_and_search(client):
    def total(**params):
        return client.get("/books", params=params).json()["total"]

    assert total(publisher="Rowohlt") == 5
    assert total(category="Krimi") == 2
    assert total(format="Taschenbuch", publisher="Kiepenheuer & Witsch") == 3
    assert total(q="FITZEK") == 1
    assert total(q="čapek") == 1  # case folding beyond ASCII
    assert total(publisher="Nobody") == 0


def test_pagination(client):
    page = client.get("/books", params={"limit": 10, "offset": 20}).json()
    assert (page["total"], len(page["items"]), page["offset"]) == (25, 5, 20)
    assert client.get("/books", params={"limit": 0}).status_code == 422


def test_get_one(client):
    slug = SEEDED[1]["slug"]
    book = client.get(f"/books/{slug}").json()
    assert book == SEEDED[1]
    assert client.get("/books/no-such-book").status_code == 404


def test_crud_round_trip(client):
    book = new_book()
    slug = book["slug"]

    created = client.post("/books", json=book)
    assert created.status_code == 201
    assert created.json() == book
    assert client.post("/books", json=book).status_code == 409
    assert client.post("/books", json=new_book(slug="other-slug")).status_code == 409  # isbn
    assert client.get("/books").json()["total"] == 26

    replaced = client.put(f"/books/{slug}", json={**book, "title": "Die dritte Sprache"})
    assert replaced.status_code == 200
    assert replaced.json() == client.get(f"/books/{slug}").json()
    assert replaced.json()["title"] == "Die dritte Sprache"

    assert client.delete(f"/books/{slug}").status_code == 204
    assert client.get(f"/books/{slug}").status_code == 404
    assert client.delete(f"/books/{slug}").status_code == 404


def test_generated_covers_are_not_writable_through_books(client):
    """Entries come from publishing a cover; book writes neither set nor clear them."""
    cover = {"id": "0123456789abcdef", "type": "suspense", "url": "/cover-images/x.png",
             "color": "#101010", "theme": SEEDED[0]["original_cover"]["theme"]}
    book = new_book()
    created = client.post("/books", json={**book, "generated_covers": [cover]}).json()
    assert created["generated_covers"] == []

    books = client.app.state.books
    book_id, _ = db.find_book(books, book["slug"])
    db.put_cover_entry(books, book_id, GeneratedCover.model_validate(cover))
    replaced = client.put(f"/books/{book['slug']}", json={**book, "generated_covers": []}).json()
    assert replaced["generated_covers"] == [cover]


def test_put_can_rename_but_not_onto_another_book(client):
    book = copy.deepcopy(SEEDED[0])
    old = book["slug"]

    clash = client.put(f"/books/{old}", json={**book, "slug": SEEDED[1]["slug"]})
    assert clash.status_code == 409
    clash = client.put(f"/books/{old}", json={**book, "isbn": SEEDED[1]["isbn"]})
    assert clash.status_code == 409

    assert client.put(f"/books/{old}", json={**book, "slug": "renamed"}).status_code == 200
    assert client.get(f"/books/{old}").status_code == 404
    assert client.get("/books/renamed").json()["isbn"] == book["isbn"]
    assert client.put("/books/missing", json=new_book()).status_code == 404


@pytest.mark.parametrize("bad", [
    {"isbn": "978-3-462-00123-4"},
    {"slug": "Not A Slug"},
    {"pages": 0},
    {"original_cover": {**SEEDED[0]["original_cover"], "color": "red"}},
    {"generated_covers": [{"id": "x", "type": "romance", "url": "u",
                           "color": "#000000", "theme": SEEDED[0]["original_cover"]["theme"]}]},
])
def test_validation(client, bad):
    assert client.post("/books", json=new_book(**bad)).status_code == 422


def test_covers_endpoints_still_mounted(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/catalogue").status_code == 200
