"""Tests that do not touch either model provider.

The art-direction and image-generation steps both degrade to deterministic
fallbacks without credentials, so the whole pipeline is exercised offline.
"""

import re

import pytest
from fastapi.testclient import TestClient

from covers.artdirection import (
    TEMPLATES,
    _genre_from_text,
    apply_overrides,
    fallback_direction,
    system_prompt,
)
from covers.formats import FORMATS, geometry, resolve_format
from covers.layout import Content, Ctx, artwork_plan, build_front
from covers.main import app
from covers.typography import TYPE_FAMILIES, face, fit_display, umlaut_leading

client = TestClient(app)


# --- Geometry ---


def test_hardcover_front_is_sized_to_the_case():
    """A jacket wraps the boards, so its front gains the Überstand."""
    hc = geometry(FORMATS["kiwi_hardcover"])
    assert hc.panel_w_mm == FORMATS["kiwi_hardcover"].trim_w_mm + 3.0
    assert hc.panel_h_mm == FORMATS["kiwi_hardcover"].trim_h_mm + 6.0
    pb = geometry(FORMATS["kiwi_paperback"])
    assert (pb.panel_w_mm, pb.panel_h_mm) == (125.0, 190.0)


def test_unknown_format_names_the_alternatives():
    with pytest.raises(KeyError, match="kiwi_paperback"):
        resolve_format("no_such_format")


# --- Typography ---


def test_display_type_fits_the_measure():
    block = fit_display(
        "DIE UNENDLICHE GESCHICHTE EINER REISE",
        "geometric",
        max_width=100.0,
        max_height=60.0,
        max_lines=4,
    )
    assert block.lines
    assert block.max_width <= 100.0 + 1e-6
    assert block.height <= 60.0


def test_fitting_prefers_larger_type_over_fewer_lines():
    one_word = fit_display(
        "KURZ", "geometric", max_width=100.0, max_height=60.0, max_lines=4
    )
    assert len(one_word.lines) == 1
    many = fit_display(
        "EIN SEHR VIEL LAENGERER TITEL MIT WORTEN",
        "geometric",
        max_width=100.0,
        max_height=120.0,
        max_lines=4,
    )
    assert len(many.lines) > 1


def test_umlaut_opens_the_leading():
    base = 0.88
    assert umlaut_leading(["SCHULD", "UND"], base) == base
    assert umlaut_leading(["SCHULD", "SÜHNE"], base) > base
    # A capital umlaut on the first line collides with nothing above it.
    assert umlaut_leading(["SÜHNE", "UND"], base) == base


@pytest.fixture
def bundled_fonts_only(monkeypatch):
    """The typeface search path of a server: no macOS faces, only src/covers/fonts."""
    import os

    from covers import typography

    monkeypatch.setattr(
        typography, "_FONT_DIRS", (os.path.join(os.path.dirname(typography.__file__), "fonts"),)
    )
    typography.face.cache_clear()
    yield
    typography.face.cache_clear()


@pytest.mark.parametrize("family", TYPE_FAMILIES)
@pytest.mark.parametrize("weight", ["display", "bold", "regular", "italic"])
def test_every_family_has_its_own_open_face(bundled_fonts_only, family, weight):
    """A deploy has no Futura or Didot; each family must still resolve to its own
    bundled face rather than silently borrowing another family's."""
    from covers.typography import FAMILIES, _find

    assert any(_find(spec.file) for spec in FAMILIES[family][weight])
    f = face(family, weight)
    for text in ("Die Übersetzerin", "STRASSE ÄÖÜ", "»Märchen« – 1999"):
        assert f.measure(text) > 0 and f.run(text, 10.0).startswith("<path")


def test_variable_faces_use_the_requested_instance(bundled_fonts_only):
    """Shaping and outlines both follow the axes: black condensed is narrower."""
    black_condensed = face("grotesk", "display")
    regular = face("grotesk", "regular")
    assert black_condensed.measure("HAMBURG") < regular.measure("HAMBURG") * 0.85


def test_blackletter_titles_are_never_set_in_capitals():
    d = fallback_direction("Ein Märchen.", "Frau Holle", "Brüder Grimm")
    pinned = apply_overrides(d.model_copy(update={"title_case": "upper"}), type_family="fraktur")
    assert pinned.title_case == "title"


def test_glyphs_are_outlined_and_scaled_to_the_em():
    f = face("geometric", "display")
    svg = f.run("HH", 10.0, fill="#000000")
    assert svg.startswith("<path")
    coords = [abs(float(v)) for v in re.findall(r"-?\d+\.?\d*", svg)[:40]]
    # Font units would put these in the thousands; mm-scaled type must not.
    assert max(coords) < 200


def test_shaping_handles_german_orthography():
    f = face("geometric", "display")
    for text in ("Öäüß", "STRASSE", "Fluß"):
        assert f.measure(text) > 0
        assert f.run(text, 10.0, fill="#000")


# --- Rendering ---


def _ctx(format_key="kiwi_paperback", title="Die Reise", **kwargs):
    direction = fallback_direction("Ein Roman über eine Reise.", title, "A. Autor")
    for k, v in kwargs.items():
        direction = direction.model_copy(update={k: v})
    geo = geometry(FORMATS[format_key])
    return Ctx(
        geo=geo,
        direction=direction,
        content=Content(
            title=title,
            author="A. Autor",
            genre_line="Roman",
            imprint="Kiepenheuer & Witsch",
        ),
        seed=42,
    )


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_renders_a_front(template):
    svg = build_front(_ctx(template=template))
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<path" in svg  # type made it in


@pytest.mark.parametrize("dpi", [72, 300])
def test_png_carries_its_resolution(dpi):
    """cairosvg writes no pHYs chunk; without one a 300 dpi file opens at 72."""
    import io

    from PIL import Image

    from covers.render import render

    ctx = _ctx()
    ctx.geo = geometry(FORMATS["kiwi_paperback"], dpi=dpi)
    with Image.open(io.BytesIO(render(ctx))) as img:
        img.load()  # a bad CRC in the spliced chunk fails here
        assert img.info["dpi"] == pytest.approx((dpi, dpi), abs=0.1)
        assert img.size == (
            round(ctx.geo.front_bleed_w_mm / 25.4 * dpi),
            round(ctx.geo.front_bleed_h_mm / 25.4 * dpi),
        )


def test_front_canvas_is_the_trim_plus_bleed():
    ctx = _ctx()
    svg = build_front(ctx)
    assert f'width="{ctx.geo.front_bleed_w_mm:.3f}mm"' in svg
    assert f'height="{ctx.geo.front_bleed_h_mm:.3f}mm"' in svg


def _path_y_range(d: str) -> tuple[float, float]:
    """Vertical extent of one SVG path, honouring H/V shorthands.

    SVGPathPen emits absolute M/L/C/Q/H/V/Z, so y values have to be picked out by
    command rather than by taking every second number.
    """
    tokens = re.findall(r"[MLCQHVZmlcqhvz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    ys: list[float] = []
    cmd = ""
    args: list[float] = []

    def flush() -> None:
        if not args:
            return
        if cmd in "Hh":
            return  # x only
        if cmd in "Vv":
            ys.extend(args)
            return
        ys.extend(args[1::2])  # y of each coordinate pair

    for token in tokens:
        if token[-1].isalpha():
            flush()
            cmd, args = token, []
        else:
            args.append(float(token))
    flush()
    return (min(ys), max(ys)) if ys else (0.0, 0.0)


@pytest.mark.parametrize(
    "format_key", ["kiwi_taschenbuch", "kiwi_paperback", "suhrkamp_taschenbuch"]
)
@pytest.mark.parametrize("title_case", ["upper", "title"])
@pytest.mark.parametrize("title", ["Die Reise", "Das letzte Haus am Deich"])
def test_kiwi_flat_type_stack_clears_the_artwork(format_key, title_case, title):
    """The whole type stack has to finish above artwork that runs to the foot.

    The title block claims the height budget it is given, so the budget has to
    reserve room for the genre line *and* the imprint below it. Without that
    reserve the imprint runs over the top edge of the artwork.
    """
    ctx = _ctx(
        format_key=format_key,
        title=title,
        template="kiwi_flat",
        artwork="procedural",
        motif="blocks",
        title_case=title_case,
    )
    svg = build_front(ctx)
    paths = re.findall(r'<path d="([^"]+)" fill="[^"]+"/>', svg)

    plan = artwork_plan(ctx.direction, ctx.geo)
    assert plan is not None
    art_top = plan[1]

    # Last two text runs on a kiwi_flat front: the genre line, then the imprint.
    genre_lo, genre_hi = _path_y_range(paths[-2])
    imprint_lo, imprint_hi = _path_y_range(paths[-1])

    assert genre_hi < imprint_lo, (
        f"genre line spans {genre_lo:.1f}-{genre_hi:.1f} mm and the imprint "
        f"{imprint_lo:.1f}-{imprint_hi:.1f} mm; they overlap"
    )
    assert imprint_hi <= art_top, (
        f"imprint reaches {imprint_hi:.2f} mm but the artwork starts at "
        f"{art_top:.2f} mm"
    )


# --- HTTP ---


def test_healthz():
    body = client.get("/healthz").json()
    assert body["status"] == "ok"


def test_catalogue_lists_the_german_formats():
    body = client.get("/catalogue").json()
    keys = {f["key"] for f in body["formats"]}
    assert {"rororo_taschenbuch", "kiwi_paperback", "kiwi_klappenbroschur"} <= keys
    assert "rororo_band" in body["templates"]


def test_generate_returns_the_image_and_geometry(output_dir):
    response = client.post(
        "/generate",
        json={
            "text": "Ein Mann verliert seine Stadt und findet eine Sprache.",
            "title": "Der Rückweg",
            "author": "Maria Braun",
            "format": "kiwi_klappenbroschur",
            "template": "kiwi_flat",
            "palette": "kobalt",
            "dpi": 72,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["image"] == f"/covers/{body['id']}/front.png"
    assert body["geometry"]["panel_mm"] == [135.0, 215.0]
    assert body["art_direction"]["template"] == "kiwi_flat"
    assert body["art_direction"]["ground"] == "#1B3A8C"

    asset = client.get(body["image"])
    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/png"


def test_generate_can_return_an_image_inline(output_dir):
    response = client.post(
        "/generate?inline=true",
        json={"text": "Kurz.", "title": "Kurz", "author": "K. Autor", "dpi": 72},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_unknown_format_is_a_422(output_dir):
    response = client.post(
        "/generate",
        json={"text": "x", "title": "x", "author": "x", "format": "nope"},
    )
    assert response.status_code == 422


def test_image_route_refuses_traversal(output_dir):
    assert client.get("/covers/abc123/../../etc/passwd").status_code == 404
    assert client.get("/covers/..%2F..%2Fetc/front.png").status_code == 404
    assert client.get("/covers/abc123/front.svg").status_code == 404


# --- Style registers ---


def test_illustrated_register_asks_for_a_picture():
    """The default register must not quietly produce a type-only cover."""
    d = fallback_direction("Zwei Schwestern erben ein Haus.", "Das Haus", "J. Wiechert", "illustrated")
    assert d.artwork == "generated"
    assert d.template in ("illustrated_full", "photo_duotone")
    # A concrete, figurative prompt -- not a generic abstract wash.
    assert len(d.image_prompt) > 80
    assert "abstract" not in d.image_prompt.lower()


def test_typographic_register_stays_austere():
    d = fallback_direction("Ein Essay über Sprache.", "Sprache", "A. Autor", "typographic")
    assert d.template in ("type_block", "kiwi_flat", "rororo_band", "didone_centre")


def test_style_conditions_the_system_prompt():
    illustrated = system_prompt("illustrated")
    typographic = system_prompt("typographic")
    assert "ILLUSTRATED AND CHARMING" in illustrated
    assert "Decoration is suspect" in typographic
    assert "Decoration is suspect" not in illustrated
    # The invariants survive in both.
    for prompt in (illustrated, typographic):
        assert "never put lettering" in prompt
        assert "Gattungsbezeichnung" in prompt


def test_illustrated_full_puts_the_type_in_a_panel_over_the_art():
    ctx = _ctx(
        title="Das letzte Haus am Deich",
        template="illustrated_full",
        artwork="procedural",
        motif="blocks",
    )
    svg = build_front(ctx)
    plan = artwork_plan(ctx.direction, ctx.geo)
    assert plan == (0.0, 0.0, ctx.geo.front_bleed_w_mm, ctx.geo.front_bleed_h_mm)
    # Ground panel over full-bleed art, then the type on top of that.
    assert svg.count("<rect") >= 2
    assert svg.count("<path") >= 4


def test_catalogue_lists_the_style_registers():
    body = client.get("/catalogue").json()
    assert body["styles"] == ["illustrated", "painterly", "typographic"]
    assert "illustrated_full" in body["templates"]


def test_genre_guess_is_not_fooled_by_german_compounds():
    """Substring matching read "verschiedene" as the poetry term "vers".

    That printed "Gedichte" on a novel. Only the no-model path uses this guess,
    which is the path chosen for speed -- so it has to be right.
    """
    assert _genre_from_text(
        "sie erinnern sich an völlig verschiedene kindheiten"
    ) == "Roman"
    # Other compounds that start with a hint term.
    assert _genre_from_text("ein versprechen an die berichtigung") == "Roman"
    # Real signals still land, including inflected forms.
    assert _genre_from_text("gesammelte gedichte") == "Gedichte"
    assert _genre_from_text("ein band mit versen") == "Gedichte"
    assert _genre_from_text("drei erzählungen") == "Erzählungen"
    assert _genre_from_text("essays über sprache") == "Essays"
    assert _genre_from_text("eine novelle") == "Novelle"
    assert _genre_from_text("die märchen der brüder grimm") == "Märchen"


def test_genre_guess_defaults_to_roman():
    assert _genre_from_text("zwei schwestern erben ein haus am hafen") == "Roman"


# --- CORS ---


@pytest.fixture(params=["covers", "bookworm"])
def cors_client(request):
    if request.param == "covers":
        return client
    from bookworm.main import app as bookworm_app

    return TestClient(bookworm_app)


def _preflight(client, origin: str):
    return client.options(
        "/generate",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "http://127.0.0.1:8080", "http://localhost"],
)
def test_browser_front_end_on_localhost_is_allowed(cors_client, origin):
    response = _preflight(cors_client, origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example.com", "http://localhost.evil.com", "https://localhost:5173"],
)
def test_other_origins_are_refused(cors_client, origin):
    """POST /generate spends money on image generation.

    A wildcard would let any page the user visits bill their OpenAI account, so
    the default has to stay narrow. Note https://localhost is refused too: the
    default regex is http-only, which is what a dev front end uses.
    """
    response = _preflight(cors_client, origin)
    assert response.headers.get("access-control-allow-origin") is None


# --- Copy ownership ---


def test_caller_pinned_copy_lands_on_the_brief_not_just_the_content():
    """One fact, one value: a pinned genre line is the brief's genre line."""
    d = fallback_direction("Ein Buch.", "T", "A")
    pinned = apply_overrides(d, genre_line="Essays")
    assert pinned.genre_line == "Essays"
    # An absent override leaves the brief alone.
    untouched = apply_overrides(d)
    assert untouched.genre_line == d.genre_line


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "fallback"),
        ({"genre_line": "Essays"}, "caller"),
    ],
)
async def test_suggestions_report_who_wrote_the_copy(kwargs, expected):
    from covers.pipeline import create_cover

    result = await create_cover(
        text="Zwei Schwestern erben ein Haus.", title="Das Haus", author="J. W.",
        director="none", artwork="none", dpi=72, **kwargs,
    )
    s = result["suggestions"]
    assert s["genre_line"]["source"] == expected
    # Whatever the source, the reported value is what the cover actually printed.
    assert s["genre_line"]["value"] == result["content"]["genre_line"]


def test_response_carries_style_and_director(output_dir):
    body = client.post(
        "/generate",
        json={"text": "Ein Haus am Hafen.", "title": "Das Haus", "author": "J. W.",
              "director": "none", "artwork": "none", "style": "typographic", "dpi": 72},
    ).json()
    assert body["style"] == "typographic"
    assert body["director"] == "none"
    assert body["suggestions"]["genre_line"]["source"] == "fallback"
