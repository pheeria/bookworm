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
from covers.lettering import Ink, Lettering
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
    """What a server has: no macOS faces, only the bundled ones."""
    from covers import typography

    monkeypatch.setenv("COVERS_SYSTEM_FONTS", "0")
    typography.face.cache_clear()
    yield
    typography.face.cache_clear()


@pytest.mark.parametrize("family", TYPE_FAMILIES)
@pytest.mark.parametrize("weight", ["display", "bold", "regular", "italic"])
def test_every_family_has_its_own_open_face(bundled_fonts_only, family, weight):
    """A deploy has no Futura or Didot; each family must still resolve to its own
    bundled face rather than silently borrowing another family's."""
    from covers.typography import BUNDLED_DIR, FAMILIES, _find

    found = [_find(spec.file) for spec in FAMILIES[family].faces[weight]]
    assert any(p and p.startswith(BUNDLED_DIR) for p in found)
    f = face(family, weight)
    for text in ("Die Übersetzerin", "STRASSE ÄÖÜ", "»Märchen« – 1999"):
        assert f.measure(text) > 0 and f.run(text, 10.0).startswith("<path")


def test_all_faces_load_at_startup(bundled_fonts_only):
    from covers.typography import check_fonts

    check_fonts()  # raises MissingFontError, which fails the app's lifespan


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


def _path_bounds(d: str) -> tuple[float, float, float, float]:
    """``(x_min, y_min, x_max, y_max)`` of one SVG path."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.svgLib.path import parse_path

    pen = BoundsPen(None)
    parse_path(d, pen)
    return pen.bounds


def _path_y_range(d: str) -> tuple[float, float]:
    """Vertical extent of one SVG path."""
    _, y_min, _, y_max = _path_bounds(d)
    return y_min, y_max


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
    # A PNG one level up, where a cover id of ".." would point; and a real one.
    (output_dir.parent / "front.png").write_bytes(b"\x89PNG outside")
    (output_dir / "abc123").mkdir()
    (output_dir / "abc123" / "front.png").write_bytes(b"\x89PNG inside")
    assert client.get("/covers/abc123/front.png").status_code == 200
    assert client.get("/covers/%2E%2E/front.png").status_code == 404
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
    from bookworm.main import create_app

    return TestClient(create_app())


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


# --- Moods ---


def test_the_brief_schema_keeps_the_moods_layouts_and_opens_every_face():
    """The directors ask the model for this schema."""
    import pydantic

    from covers.artdirection import ArtDirection, brief_schema
    from covers.moods import MOODS

    suspense = MOODS["suspense"]
    schema = brief_schema(suspense)
    props = schema.model_json_schema()["properties"]
    # One allowed value is a const in JSON Schema, several an enum; both hold the model.
    template = props["template"]
    assert template.get("enum", [template.get("const")]) == list(suspense.templates)
    brief = fallback_direction("Ein Mord.", "Nacht", "A. Autor", "painterly", suspense).model_dump()
    assert schema.model_validate({**brief, "type_family": "garalde"})  # no suspense face, allowed
    with pytest.raises(pydantic.ValidationError):
        schema.model_validate({**brief, "type_family": "comic_sans"})
    assert brief_schema() is ArtDirection


def test_the_mood_is_named_in_the_prompt():
    from covers.moods import MOODS

    prompt = system_prompt("painterly", MOODS["suspense"])
    assert "crime, thriller" in prompt
    assert "Who this cover is for" not in system_prompt("painterly")


def test_generate_accepts_a_mood(output_dir):
    body = client.post(
        "/generate",
        json={"text": "Ein Mord im Hafen.", "title": "Nacht", "author": "A. Autor",
              "mood": "suspense", "director": "none", "dpi": 72},
    ).json()
    assert (body["mood"], body["style"]) == ("suspense", "painterly")
    from covers.moods import MOODS

    assert body["art_direction"]["type_family"] in MOODS["suspense"].type_families


# --- The picture layout: type straight on the image ---


@pytest.mark.parametrize(("sky", "expected"), [((30, 32, 60), "#fbf8f3"), ((235, 230, 215), "#2a2a2a")])
def test_type_on_the_picture_takes_a_colour_that_reads_on_it(sky, expected):
    from PIL import Image

    from covers.formats import px

    ctx = _ctx(template="picture", artwork="generated", lettering=Lettering(
        title_ink=Ink(color="#2a2a2a"), text_ink=Ink(color="#2a2a2a")))
    plan = artwork_plan(ctx.direction, ctx.geo)
    ctx.artwork_image = Image.new("RGB", (px(plan[2], 60), px(plan[3], 60)), sky)
    svg = build_front(ctx)
    assert f'fill="{expected}"' in svg
    # Nothing is drawn behind the type: the only rect is the canvas ground.
    assert svg.count("<rect") == 1


def test_the_rasteriser_is_handed_the_artwork():
    import io

    from PIL import Image

    from covers.render import render

    ctx = _ctx(template="picture", artwork="generated", lettering=Lettering(location="top"))
    ctx.artwork_image = Image.new("RGB", (200, 300), (200, 30, 30))
    ctx.geo = geometry(FORMATS["kiwi_paperback"], dpi=72)
    with Image.open(io.BytesIO(render(ctx))) as png:
        r, g, b = png.convert("RGB").getpixel((png.width // 2, png.height * 3 // 4))
    assert r > 150 and g < 80 and b < 80  # the picture, not the ground


def test_the_picture_zone_moves_the_type():
    def title_top(zone):
        svg = build_front(_ctx(template="picture", lettering=Lettering(location=zone), artwork="none"))
        paths = re.findall(r'<path d="([^"]+)"', svg)
        return _path_y_range(paths[1])[0]  # author, then title

    assert title_top("top") < title_top("bottom")


@pytest.mark.parametrize("location", ["left", "right"])
def test_a_side_column_keeps_the_type_on_its_side(location):
    ctx = _ctx(template="picture", artwork="none", title="Die Nacht der langen Schatten",
               lettering=Lettering(location=location, size="large"))
    svg = build_front(ctx)
    middle = ctx.geo.bleed_mm + ctx.geo.panel_w_mm / 2
    # Author, title lines and genre; the imprint (last) stays centred at the foot.
    for d in re.findall(r'<path d="([^"]+)"', svg)[:-1]:
        x_min, _, x_max, _ = _path_bounds(d)
        assert x_max < middle if location == "left" else x_min > middle


def test_a_diagonal_title_is_rotated_and_the_rest_is_not():
    svg = build_front(_ctx(template="picture", artwork="none",
                           lettering=Lettering(location="diagonal", angle=-25)))
    rotated = re.findall(r'<g transform="rotate\(-25 [^"]+">(.*?)</g>', svg)
    assert len(rotated) == 1 and "<path" in rotated[0]
    assert svg.count("<path") > rotated[0].count("<path")  # author, genre, imprint outside


def test_a_title_gradient_is_one_gradient_across_its_lines():
    ink = Ink(color="#ffd27a", gradient_to="#ff6a3d")
    svg = build_front(_ctx(template="picture", artwork="none", ground="#14203a",
                           title="Die Nacht der langen Schatten", lettering=Lettering(title_ink=ink)))
    assert svg.count("<linearGradient") == 1
    assert 'stop-color="#ffd27a"' in svg and 'stop-color="#ff6a3d"' in svg
    assert svg.count('fill="url(#title-ink)"') >= 2


def test_a_gradient_that_would_not_read_falls_back_to_one_colour():
    ink = Ink(color="#1a1a2a", gradient_to="#20304a")  # dark on a dark ground
    svg = build_front(_ctx(template="picture", artwork="none", ground="#14203a",
                           lettering=Lettering(title_ink=ink)))
    assert "<linearGradient" not in svg and 'fill="#fbf8f3"' in svg


def test_each_moods_brief_describes_every_family_and_names_its_own():
    from covers.artdirection import brief_schema
    from covers.concepts import concepts_schema
    from covers.moods import MOODS
    from covers.typography import FAMILIES

    heart = MOODS["heart"]
    for description in (
        brief_schema(heart).model_fields["type_family"].description,
        concepts_schema(heart).model_json_schema()["$defs"]["Concept_heart"]["properties"]["type_family"]["description"],
    ):
        assert all(f"{family}: " in description for family in FAMILIES)
        assert f"Suited to this reader type: {', '.join(heart.type_families)}." in description


def test_casing_follows_each_family():
    d = fallback_direction("Ein Buch.", "T", "A")
    assert apply_overrides(d.model_copy(update={"title_case": "upper"}), type_family="grenze_gotisch").title_case == "title"
    from covers.artdirection import _title_case

    assert (_title_case("bebas"), _title_case("lora")) == ("upper", "title")


# --- Contrast between the type and the picture ---


def _busy(ctx):
    """A detailed, mid-toned picture covering the artwork plan: the worst case for type."""
    from PIL import Image

    from covers.formats import px

    plan = artwork_plan(ctx.direction, ctx.geo)
    w, h = px(plan[2], 100), px(plan[3], 100)
    return Image.effect_noise((w // 6, h // 6), 120).convert("RGB").resize((w, h))


@pytest.mark.parametrize("location", ["top", "right", "diagonal"])
def test_every_line_reads_on_a_busy_picture(location):
    """No line is left below its contrast: the picture is toned under it until it reads."""
    from covers import contrast
    from covers.layout import compose
    from covers.palettes import contrast_ratio

    lettering = Lettering(location=location, author_location="bottom")
    ctx = _ctx(template="picture", artwork="generated", title="Die Nacht der langen Schatten", lettering=lettering)
    ctx.artwork_image = _busy(ctx)
    _, toned = compose(ctx)
    assert ctx.burns  # nothing reads on noise as it is
    plan = artwork_plan(ctx.direction, ctx.geo)
    for burn in ctx.burns:
        tones = contrast.tones(contrast.sample(toned), burn.block, plan)
        assert min(contrast_ratio(burn.ink, t) for t in tones) >= burn.need


def test_a_calm_picture_is_left_alone():
    from PIL import Image

    from covers.layout import compose

    ctx = _ctx(template="picture", artwork="generated", lettering=Lettering(
        title_ink=Ink(color="#fbf8f3"), text_ink=Ink(color="#fbf8f3")))
    plan = artwork_plan(ctx.direction, ctx.geo)
    ctx.artwork_image = Image.new("RGB", (int(plan[2] * 4), int(plan[3] * 4)), (20, 24, 40))
    _, toned = compose(ctx)
    assert not ctx.burns and toned is ctx.artwork_image


def test_the_author_can_stand_apart_from_the_title():
    ctx = _ctx(template="picture", artwork="none", lettering=Lettering(location="top", author_location="bottom"))
    paths = re.findall(r'<path d="([^"]+)"', build_front(ctx))
    author, title = _path_y_range(paths[0]), _path_y_range(paths[1])
    assert author[0] > title[1] + ctx.geo.panel_h_mm / 2  # the author is down at the foot


def test_the_image_prompt_keeps_the_authors_edge_calm():
    from covers.lettering import zone_text

    apart = zone_text(Lettering(location="top", author_location="bottom"))
    assert "author's name along the bottom edge" in apart
    assert "the author, title and genre" in zone_text(Lettering(location="top"))



def test_a_house_palette_brings_its_colours():
    from covers.artdirection import ArtDirection
    from covers.palettes import PALETTES_BY_KEY

    brief = fallback_direction("Ein Roman.", "Titel", "A. Autor").model_dump()
    pinned = ArtDirection.model_validate({**brief, "house_palette": "kobalt"})
    kobalt = PALETTES_BY_KEY["kobalt"]
    assert (pinned.ground, pinned.ink, pinned.accent) == (kobalt.ground, kobalt.ink, kobalt.accent)
    assert apply_overrides(fallback_direction("x", "y", "z"), palette_key="kobalt").ground == kobalt.ground


@pytest.mark.parametrize(("location", "asked", "settled"), [
    ("top", "top", "with_title"),          # the title already holds the top edge
    ("left", "top", "with_title"),
    ("bottom", "bottom", "with_title"),
    ("top", "bottom", "bottom"),
    ("diagonal", "with_title", "top"),     # a diagonal title takes the middle
])
def test_the_authors_place_is_settled_once(location, asked, settled):
    from covers.lettering import zone_text

    lettering = Lettering(location=location, author_location=asked)
    assert lettering.author_location == settled
    assert ("author's name along" in zone_text(lettering)) == (settled != "with_title")
