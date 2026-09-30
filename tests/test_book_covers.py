"""Covers for books, against in-memory mongomock with GridFS.

Generation runs for real but offline: the provider keys are cleared, so the brief
is the deterministic fallback and the artwork a procedural motif. ``FAST`` keeps
each render small.
"""

import json

import mongomock
import mongomock.gridfs
import pytest
from fastapi.testclient import TestClient

from books.db import SEED
from books.models import Book
from bookworm.book_covers import book_defaults, format_for, theme_for
from bookworm.main import create_app

mongomock.gridfs.enable_gridfs_integration()

SEEDED = json.loads(SEED.read_text(encoding="utf-8"))
KIWI_HARDCOVER = SEEDED[0]  # Eva Menasse, Alleinruhelage
SLUG = KIWI_HARDCOVER["slug"]
FAST = {"dpi": 72, "director": "none", "artwork": "procedural"}


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(mongomock.MongoClient)) as c:
        yield c


def create(client, slug=SLUG, **body):
    response = client.post(f"/books/{slug}/covers", json={"type": "heart", **FAST, **body})
    assert response.status_code == 201, response.text
    return response.json()


def book_entries(client, slug=SLUG):
    return client.get(f"/books/{slug}").json()["generated_covers"]


# --- Book details -> request ---


def test_defaults_come_from_the_book():
    book = Book.model_validate(KIWI_HARDCOVER)
    d = book_defaults(book)
    assert (d["title"], d["author"], d["imprint"]) == (book.title, book.author, book.publisher)
    assert d["genre_line"] == "Roman"
    assert d["format"] == "kiwi_hardcover"
    # "Roman" as a subtitle says nothing the genre line doesn't.
    assert d["text"] == book.blurb


def test_format_falls_back_by_binding():
    def fmt(publisher, format):
        return format_for(Book.model_validate({**KIWI_HARDCOVER, "publisher": publisher, "format": format}))

    assert fmt("Rowohlt", "Taschenbuch") == "rororo_taschenbuch"
    assert fmt("S. FISCHER", "Hardcover") == "din_a5_hardcover"
    assert fmt("S. FISCHER", "Paperback") == "kiwi_paperback"


@pytest.mark.parametrize(("color", "dark"), [("#292512", True), ("#4b70b6", True), ("#b96d5a", False)])
def test_theme_follows_the_seed_rules(color, dark):
    seed = next(b["original_cover"]["theme"] for b in SEEDED if b["original_cover"]["color"] == color)
    theme = theme_for(color).model_dump()
    assert ("fbf8f3" in theme["text"]) == dark == ("fbf8f3" in seed["text"])
    assert theme["ring"] == seed["ring"]


# --- Lifecycle ---


def test_create_makes_a_draft_from_the_book(client):
    cover = create(client)
    assert cover["status"] == "draft" and cover["book"] == SLUG
    assert cover["content"]["title"] == KIWI_HARDCOVER["title"]
    assert cover["geometry"]["format"]["key"] == "kiwi_hardcover"
    assert cover["options"] == FAST  # only what the caller set
    assert cover["url"] == f"/cover-images/{cover['id']}.png"

    image = client.get(cover["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"

    # A draft is not on the book.
    assert book_entries(client) == []


def test_body_overrides_the_book(client):
    cover = create(client, title="Anderer Titel", format="kiwi_paperback", type="suspense")
    assert cover["content"]["title"] == "Anderer Titel"
    assert cover["geometry"]["format"]["key"] == "kiwi_paperback"
    assert cover["type"] == "suspense"


def test_publish_puts_the_short_entry_on_the_book(client):
    cover = create(client)
    published = client.post(f"/books/{SLUG}/covers/{cover['id']}/publish").json()
    assert published["status"] == "published" and published["published_at"]
    assert book_entries(client) == [
        {k: cover[k] for k in ("id", "type", "url", "color", "theme")}
    ]
    # Publishing twice does not duplicate the entry.
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    assert len(book_entries(client)) == 1


def test_patch_refreshes_a_published_entry(client):
    cover = create(client)
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    patched = client.patch(
        f"/books/{SLUG}/covers/{cover['id']}", json={"type": "discourse", "color": "#123456"}
    ).json()
    assert (patched["type"], patched["color"]) == ("discourse", "#123456")
    assert patched["art_direction"] == cover["art_direction"]  # nothing regenerated
    [entry] = book_entries(client)
    assert (entry["type"], entry["color"]) == ("discourse", "#123456")


def test_patch_of_a_draft_leaves_the_book_alone(client):
    cover = create(client)
    client.patch(f"/books/{SLUG}/covers/{cover['id']}", json={"type": "trend"})
    assert book_entries(client) == []


def test_regenerate_merges_options_and_reads_the_current_book(client):
    cover = create(client, style="typographic")
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    old_image = client.get(cover["url"]).content

    # The book changes after the cover was made.
    client.put(f"/books/{SLUG}", json={**KIWI_HARDCOVER, "title": "Neuer Titel"})

    again = client.post(
        f"/books/{SLUG}/covers/{cover['id']}/regenerate", json={"palette": "kobalt"}
    ).json()
    assert again["id"] == cover["id"]
    assert again["options"] == {**FAST, "style": "typographic", "palette": "kobalt"}
    assert again["style"] == "typographic"
    assert again["art_direction"]["ground"] == "#1B3A8C"
    assert again["content"]["title"] == "Neuer Titel"
    assert client.get(again["url"]).content != old_image
    [entry] = book_entries(client)
    assert entry["color"] == "#1b3a8c"


def test_covers_survive_a_slug_rename(client):
    cover = create(client)
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    renamed = f"renamed-{KIWI_HARDCOVER['isbn']}"
    client.put(f"/books/{SLUG}", json={**KIWI_HARDCOVER, "slug": renamed})

    assert client.get(f"/books/{renamed}/covers/{cover['id']}").status_code == 200
    assert client.get(book_entries(client, renamed)[0]["url"]).status_code == 200


def test_unpublish_removes_the_entry(client):
    cover = create(client)
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    draft = client.post(f"/books/{SLUG}/covers/{cover['id']}/unpublish").json()
    assert draft["status"] == "draft" and draft["published_at"] is None
    assert book_entries(client) == []


def test_delete_removes_cover_entry_and_image(client):
    cover = create(client)
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    assert client.delete(f"/books/{SLUG}/covers/{cover['id']}").status_code == 204
    assert book_entries(client) == []
    assert client.get(f"/books/{SLUG}/covers/{cover['id']}").status_code == 404
    assert client.get(cover["url"]).status_code == 404
    assert client.delete(f"/books/{SLUG}/covers/{cover['id']}").status_code == 404


def test_list_filters_by_status_and_type(client):
    a = create(client, type="heart")
    create(client, type="suspense")
    client.post(f"/books/{SLUG}/covers/{a['id']}/publish")

    def ids(**params):
        return [c["id"] for c in client.get(f"/books/{SLUG}/covers", params=params).json()]

    assert len(ids()) == 2
    assert ids(status="published") == [a["id"]]
    assert ids(type="heart") == [a["id"]]


def test_covers_belong_to_their_book(client):
    cover = create(client)
    other = SEEDED[1]["slug"]
    assert client.get(f"/books/{other}/covers/{cover['id']}").status_code == 404
    assert client.get(f"/books/{other}/covers").json() == []


# --- Errors ---


def test_unknown_book_is_a_404(client):
    assert client.post("/books/nope/covers", json={"type": "heart"}).status_code == 404
    assert client.get("/books/nope/covers").status_code == 404


def test_bad_requests_are_422(client):
    base = f"/books/{SLUG}/covers"
    assert client.post(base, json={"type": "romance"}).status_code == 422
    assert client.post(base, json={"type": "heart", **FAST, "format": "nope"}).status_code == 422
    assert client.patch(f"{base}/x", json={"color": "red"}).status_code == 422
