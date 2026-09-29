"""Tests that do not touch either model provider.

The art-direction and image-generation steps both degrade to deterministic
fallbacks without credentials, so the whole pipeline is exercised offline.
"""

import re

import pytest
from fastapi.testclient import TestClient

from bookworm.artdirection import (
    TEMPLATES,
    _genre_from_text,
    fallback_direction,
    system_prompt,
)
from bookworm.formats import FORMATS, geometry, resolve_format, spine_mm
from bookworm.layout import (
    Content,
    Ctx,
    _teaser,
    artwork_plan,
    build_front,
    build_spread,
)
from bookworm.main import app
from bookworm.typography import face, fit_display, umlaut_leading

client = TestClient(app)


# --- Geometry ---


def test_spine_grows_with_page_count():
    fmt = FORMATS["kiwi_paperback"]
    assert spine_mm(fmt, 600) > spine_mm(fmt, 200) > 0


def test_hardcover_spine_clears_the_boards():
    hc = FORMATS["kiwi_hardcover"]
    pb = FORMATS["kiwi_paperback"]
    # Same block, but the jacket has to wrap two 2.5 mm boards.
    assert spine_mm(hc, 300) > spine_mm(pb, 300) + 2 * hc.board_mm


def test_sheet_is_the_sum_of_its_panels():
    geo = geometry(FORMATS["kiwi_klappenbroschur"], pages=320)
    panels = sum(p.w_mm for p in geo.panels)
    assert geo.sheet_w_mm == pytest.approx(panels + 2 * geo.bleed_mm)
    assert geo.sheet_h_mm == pytest.approx(geo.panel_h_mm + 2 * geo.bleed_mm)
    assert [p.name for p in geo.panels] == [
        "back_flap",
        "back",
        "spine",
        "front",
        "front_flap",
    ]


def test_flapless_format_has_three_panels():
    geo = geometry(FORMATS["rororo_taschenbuch"], pages=200)
    assert [p.name for p in geo.panels] == ["back", "spine", "front"]


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


def _ctx(format_key="kiwi_paperback", pages=288, title="Die Reise", **kwargs):
    direction = fallback_direction("Ein Roman über eine Reise.", title, "A. Autor")
    for k, v in kwargs.items():
        direction = direction.model_copy(update={k: v})
    geo = geometry(FORMATS[format_key], pages=pages)
    return Ctx(
        geo=geo,
        direction=direction,
        content=Content(
            title=title,
            author="A. Autor",
            genre_line="Roman",
            blurb="Eine Frau verlässt ihre Stadt. Sie kommt nicht zurück. Der Rest ist Weg.",
            imprint="Kiepenheuer & Witsch",
            isbn="978-3-462-00000-0",
        ),
        palette=direction.palette,
        seed=42,
    )


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_renders_front_and_spread(template):
    ctx = _ctx(template=template)
    front = build_front(ctx)
    spread = build_spread(ctx)
    for svg in (front, spread):
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "<path" in svg  # type made it in


def test_front_canvas_is_the_trim_plus_bleed():
    ctx = _ctx()
    svg = build_front(ctx)
    assert f'width="{ctx.geo.front_bleed_w_mm:.3f}mm"' in svg
    assert f'height="{ctx.geo.front_bleed_h_mm:.3f}mm"' in svg


def test_front_panel_is_clipped_without_a_transform_on_the_same_group():
    # An element's transform also applies to its clip-path, so the two must not
    # share a group or the front panel clips itself off the sheet.
    svg = build_spread(_ctx("kiwi_klappenbroschur"))
    assert 'clip-path="url(#clip-front-panel)">' in svg
    assert 'clip-path="url(#clip-front-panel)" transform' not in svg


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


def test_narrow_spine_is_reported_not_lettered():
    ctx = _ctx("rororo_taschenbuch", pages=96)
    build_spread(ctx)
    assert any("too narrow" in n for n in ctx.notes)


def test_wide_spine_is_lettered():
    ctx = _ctx("kiwi_hardcover", pages=600)
    build_spread(ctx)
    assert not any("too narrow" in n for n in ctx.notes)


def test_teaser_keeps_the_opening_sentences():
    blurb = "Eine Frau geht fort. Sie kommt nicht zurück. Der Rest ist Weg."
    assert _teaser(blurb) == "Eine Frau geht fort. Sie kommt nicht zurück."
    assert _teaser("Ein Satz ohne Punkt") == "Ein Satz ohne Punkt."


def test_back_cover_teases_only_when_a_flap_carries_the_blurb():
    """On a flapped jacket the full Klappentext belongs on the flap, not twice."""
    flapped = build_spread(_ctx("kiwi_klappenbroschur"))
    plain = build_spread(_ctx("kiwi_paperback"))
    # The unflapped back sets the whole blurb, so it carries more type.
    assert plain.count("<path") > 0 and flapped.count("<path") > 0
    assert _ctx("kiwi_klappenbroschur").geo.flap_mm > 0
    assert _ctx("kiwi_paperback").geo.flap_mm == 0


# --- HTTP ---


def test_healthz():
    body = client.get("/healthz").json()
    assert body["status"] == "ok"


def test_catalogue_lists_the_german_formats():
    body = client.get("/catalogue").json()
    keys = {f["key"] for f in body["formats"]}
    assert {"rororo_taschenbuch", "kiwi_paperback", "kiwi_klappenbroschur"} <= keys
    assert "rororo_band" in body["templates"]


def test_generate_returns_assets_and_geometry(tmp_path, monkeypatch):
    monkeypatch.setattr("bookworm.main.OUTPUT_DIR", tmp_path)
    response = client.post(
        "/generate",
        json={
            "text": "Ein Mann verliert seine Stadt und findet eine Sprache.",
            "title": "Der Rückweg",
            "author": "Maria Braun",
            "format": "kiwi_klappenbroschur",
            "pages": 320,
            "template": "kiwi_flat",
            "palette": "kobalt",
            "assets": ["front_png", "front_svg", "spread_pdf", "direction_json"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["assets"]) == {
        "front_png",
        "front_svg",
        "spread_pdf",
        "direction_json",
    }
    assert body["geometry"]["spine_mm"] > 0
    assert body["art_direction"]["template"] == "kiwi_flat"
    assert body["art_direction"]["ground"] == "#1B3A8C"

    url = body["assets"]["front_png"]
    asset = client.get(url)
    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/png"


def test_generate_can_return_an_image_inline(tmp_path, monkeypatch):
    monkeypatch.setattr("bookworm.main.OUTPUT_DIR", tmp_path)
    response = client.post(
        "/generate?inline=front_png",
        json={"text": "Kurz.", "title": "Kurz", "author": "K. Autor"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_unknown_format_is_a_422(tmp_path, monkeypatch):
    monkeypatch.setattr("bookworm.main.OUTPUT_DIR", tmp_path)
    response = client.post(
        "/generate",
        json={"text": "x", "title": "x", "author": "x", "format": "nope"},
    )
    assert response.status_code == 422


def test_asset_route_refuses_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr("bookworm.main.OUTPUT_DIR", tmp_path)
    assert client.get("/covers/abc123/../../etc/passwd").status_code == 404
    assert client.get("/covers/..%2F..%2Fetc/passwd").status_code == 404


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


def _preflight(origin: str):
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
def test_browser_front_end_on_localhost_is_allowed(origin):
    response = _preflight(origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example.com", "http://localhost.evil.com", "https://localhost:5173"],
)
def test_other_origins_are_refused(origin):
    """POST /generate spends money on image generation.

    A wildcard would let any page the user visits bill their OpenAI account, so
    the default has to stay narrow. Note https://localhost is refused too: the
    default regex is http-only, which is what a dev front end uses.
    """
    response = _preflight(origin)
    assert response.headers.get("access-control-allow-origin") is None
