"""Covers for books, against in-memory mongomock with GridFS.

Generation runs for real but offline: conftest makes the deterministic brief the
director and clears the image key, so the artwork falls back to a procedural motif.
"""

import io
import json

import pytest
from PIL import Image, ImageDraw

from books.db import SEED
from books.models import Book, GeneratedCover
from bookworm.book_covers import book_defaults, theme_for
from bookworm.houses import formalities
from covers.artdirection import brief_schema, fallback_direction
from covers.moods import MOODS

SEEDED = json.loads(SEED.read_text(encoding="utf-8"))
KIWI_HARDCOVER = SEEDED[0]  # Eva Menasse, Alleinruhelage
SLUG = KIWI_HARDCOVER["slug"]


def create(client, slug=SLUG, **body):
    response = client.post(f"/books/{slug}/covers", json={"type": "heart", **body})
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


def test_the_publisher_fixes_the_formalities():
    def house(publisher, format):
        return formalities(Book.model_validate({**KIWI_HARDCOVER, "publisher": publisher, "format": format}))

    assert house("Rowohlt", "Taschenbuch") == {"format": "rororo_taschenbuch", "imprint": "rororo"}
    assert house("Rowohlt", "Hardcover")["imprint"] == "Rowohlt"
    assert house("S. FISCHER", "Hardcover")["format"] == "din_a5_hardcover"
    assert house("S. FISCHER", "Paperback")["format"] == "kiwi_paperback"
    assert house("Unbekannt", "Paperback")["imprint"] == "Unbekannt"


@pytest.mark.parametrize("type", list(MOODS))
@pytest.mark.parametrize("book", SEEDED, ids=lambda b: b["slug"][:24])
def test_every_book_briefs_inside_its_reader_type(type, book):
    """No model: the deterministic brief for each book stays inside the mood."""
    mood = MOODS[type]
    d = book_defaults(Book.model_validate(book))
    brief = fallback_direction(d["text"], d["title"], d["author"], mood.style, mood)
    assert brief_schema(mood).model_validate(brief.model_dump())


@pytest.mark.parametrize("type", list(MOODS))
def test_the_reader_type_decides_mood_and_faces(client, type):
    mood = MOODS[type]
    book = SEEDED[list(MOODS).index(type) * 5]  # a different house for each
    cover = create(client, slug=book["slug"], type=type)
    assert cover["mood"] == type and cover["style"] == mood.style
    assert cover["art_direction"]["template"] in mood.templates
    assert cover["art_direction"]["type_family"] in mood.type_families
    # The formalities are the publisher's, whatever the type.
    assert cover["content"]["imprint"] == formalities(Book.model_validate(book))["imprint"]


def test_only_the_type_and_text_can_be_chosen(client):
    base = f"/books/{SLUG}/covers"
    for extra in ({"template": "type_block"}, {"type_family": "fraktur"}, {"format": "kiwi_paperback"}):
        # Unknown fields are ignored, not applied.
        cover = create(client, **extra)
        assert cover["options"] == {}
    assert client.post(base, json={"type": "heart", "text": ""}).status_code == 422


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
    assert cover["options"] == {}  # the caller set nothing but the type
    assert cover["url"].startswith("/cover-images/")

    image = client.get(cover["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    # Each render gets a new image id, so the URL's content never changes.
    assert "immutable" in image.headers["cache-control"]

    # A draft is not on the book.
    assert book_entries(client) == []


def test_the_brief_text_can_be_rewritten(client):
    cover = create(client, text="Eine Frau verschwindet im Nebel über dem Hafen.")
    assert cover["options"] == {"text": "Eine Frau verschwindet im Nebel über dem Hafen."}
    assert cover["request"]["text"] == cover["options"]["text"]
    assert cover["content"]["title"] == KIWI_HARDCOVER["title"]  # still the book's


def test_publish_puts_the_short_entry_on_the_book(client):
    cover = create(client)
    published = client.post(f"/books/{SLUG}/covers/{cover['id']}/publish").json()
    assert published["status"] == "published" and published["published_at"]
    assert book_entries(client) == [GeneratedCover.model_validate(cover).model_dump()]
    # Publishing twice does not duplicate the entry.
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    assert len(book_entries(client)) == 1


def test_patch_refreshes_a_published_entry(client):
    cover = create(client)
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    patched = client.patch(
        f"/books/{SLUG}/covers/{cover['id']}", json={"color": "#123456"}
    ).json()
    assert patched["color"] == "#123456"
    assert patched["art_direction"] == cover["art_direction"]  # nothing regenerated
    [entry] = book_entries(client)
    assert entry["color"] == "#123456"


def test_patch_of_a_draft_leaves_the_book_alone(client):
    cover = create(client)
    client.patch(f"/books/{SLUG}/covers/{cover['id']}", json={"color": "#123456"})
    assert book_entries(client) == []


def test_regenerate_reads_the_current_book_and_can_change_type(client):
    cover = create(client, type="heart", text="Ein Sommer am Meer.")
    client.post(f"/books/{SLUG}/covers/{cover['id']}/publish")
    old_image = client.get(cover["url"]).content

    # The book changes after the cover was made.
    client.put(f"/books/{SLUG}", json={**KIWI_HARDCOVER, "title": "Neuer Titel"})

    again = client.post(
        f"/books/{SLUG}/covers/{cover['id']}/regenerate", json={"type": "suspense"}
    ).json()
    assert again["id"] == cover["id"]
    assert again["type"] == again["mood"] == "suspense"
    assert again["style"] == MOODS["suspense"].style
    assert again["art_direction"]["type_family"] in MOODS["suspense"].type_families
    assert again["options"] == {"text": "Ein Sommer am Meer."}  # kept
    assert again["content"]["title"] == "Neuer Titel"
    assert client.get(again["url"]).content != old_image
    [entry] = book_entries(client)
    assert (entry["type"], entry["color"]) == ("suspense", again["color"])

    # No body at all just re-renders as it is.
    same = client.post(f"/books/{SLUG}/covers/{cover['id']}/regenerate")
    assert same.status_code == 200 and same.json()["type"] == "suspense"


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
    assert client.patch(f"{base}/x", json={"color": "red"}).status_code == 422


# --- Uploads ---


def _image(color="#1b3a8c", fmt="JPEG") -> bytes:
    img = Image.new("RGB", (300, 450), color)
    ImageDraw.Draw(img).rectangle((40, 60, 260, 120), fill="#f4f1e8")  # a title panel
    out = io.BytesIO()
    img.save(out, format=fmt)
    return out.getvalue()


def upload(client, title=KIWI_HARDCOVER["title"], type="suspense", data=None):
    return client.post(
        "/covers/upload",
        data={"title": title, "type": type},
        files={"file": ("cover", data or _image())},
    )


def test_an_uploaded_cover_is_published_on_its_book(client):
    response = upload(client, title="  alleinruhelage ")  # case and spacing ignored
    assert response.status_code == 201, response.text
    cover = response.json()
    assert (cover["book"], cover["type"], cover["status"]) == (SLUG, "suspense", "published")
    assert cover["published_at"]
    # An upload is its own variant: no brief, geometry or request at all.
    assert cover["source"] == "uploaded" and "art_direction" not in cover and "request" not in cover
    # The colour is the image's ground, and the theme follows from it.
    assert cover["color"] == "#1b3a8c"
    assert cover["theme"] == theme_for("#1b3a8c").model_dump()

    image = client.get(cover["url"])
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"  # stored as PNG, whatever came in

    assert [c["id"] for c in client.get(f"/books/{SLUG}/covers").json()] == [cover["id"]]
    # Straight onto the book, where the UI reads covers from.
    assert book_entries(client) == [GeneratedCover.model_validate(cover).model_dump()]
    client.post(f"/books/{SLUG}/covers/{cover['id']}/unpublish")
    assert book_entries(client) == []


def test_a_light_upload_gets_a_light_theme(client):
    png = _image("#f2ead3", fmt="PNG")
    cover = upload(client, data=png).json()
    assert cover["color"] == "#f2ead3"
    assert "fbf8f3" not in cover["theme"]["text"]  # dark type on a light page
    assert client.get(cover["url"]).content == png  # a PNG upload is stored as sent


def test_regenerating_an_upload_renders_it_afresh(client):
    cover = upload(client).json()
    again = client.post(f"/books/{SLUG}/covers/{cover['id']}/regenerate").json()
    assert again["source"] == "generated" and again["art_direction"]
    assert again["type"] == "suspense"
    assert client.get(cover["url"]).status_code == 404  # the upload is replaced
    [entry] = book_entries(client)  # and the book shows the new render
    assert entry["url"] == again["url"]


def test_upload_title_must_name_exactly_one_book(client):
    assert upload(client, title="Kein solches Buch").status_code == 404
    twin = {**SEEDED[1], "title": KIWI_HARDCOVER["title"],
            "slug": "zwilling-9783462006049", "isbn": "9783462006049"}
    assert client.post("/books", json=twin).status_code == 201
    response = upload(client)
    assert response.status_code == 409
    assert SLUG in response.json()["detail"] and twin["slug"] in response.json()["detail"]


def test_upload_refuses_what_is_not_a_cover(client):
    assert upload(client, data=b"not an image").status_code == 422
    assert upload(client, type="romance").status_code == 422
    assert client.post("/covers/upload", data={"title": "Alleinruhelage", "type": "heart"}).status_code == 422
    assert client.get(f"/books/{SLUG}/covers").json() == []  # nothing was stored


def test_covers_stored_before_uploads_read_as_generated(client):
    cover = create(client)
    client.app.state.covers.update_one({"id": cover["id"]}, {"$unset": {"source": ""}})
    [listed] = client.get(f"/books/{SLUG}/covers").json()
    assert listed["source"] == "generated" and listed["art_direction"]


def test_the_schema_tells_generated_and_uploaded_covers_apart(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    generated, uploaded = schemas["GeneratedBookCover"], schemas["UploadedBookCover"]
    assert {"art_direction", "geometry", "request", "seed"} <= set(generated["required"])
    assert "art_direction" not in uploaded["properties"]
