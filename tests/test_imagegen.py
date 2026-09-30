"""The artwork path, for each image model, exercised against stub clients.

These tests cover the plumbing around the call -- prompt hardening, cropping to
the planned placement, the duotone treatment, embedding, the effective-dpi report,
and the size arguments each fal model takes. They call neither OpenAI nor fal.
"""

import base64
import io

import pytest
from PIL import Image

from covers import imagegen
from covers.formats import FORMATS, geometry
from covers.imagegen import build_prompt
from covers.layout import artwork_plan
from covers.pipeline import create_cover


class _StubImage:
    def __init__(self, b64: str) -> None:
        self.b64_json = b64
        self.revised_prompt = "stubbed"


class _StubResponse:
    def __init__(self, b64: str) -> None:
        self.data = [_StubImage(b64)]


class _StubImages:
    def __init__(self, recorder: dict) -> None:
        self._recorder = recorder

    async def generate(self, **kwargs):
        self._recorder.update(kwargs)
        # A recognisable gradient so the duotone mapping is observable.
        img = Image.linear_gradient("L").convert("RGB").resize((1024, 1536))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return _StubResponse(base64.b64encode(buf.getvalue()).decode())


class _StubClient:
    def __init__(self, recorder: dict) -> None:
        self.images = _StubImages(recorder)


@pytest.fixture
def stub_openai(monkeypatch):
    recorder: dict = {}
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kw: _StubClient(recorder))
    return recorder


async def test_generate_crops_to_the_requested_pixels(stub_openai):
    art = await imagegen.generate(
        "A red door in fog",
        ("#101A2C", "#EDE7DA"),
        target_w_px=1500,
        target_h_px=900,
        model="openai",
        treatment="none",
    )
    assert art is not None
    assert art.image.size == (1500, 900)
    assert art.meta["native_px"] == [1024, 1536]
    # A landscape target must pick the landscape size, not the portrait default.
    assert stub_openai["size"] == "1536x1024"
    assert "no letters" in stub_openai["prompt"]


async def test_duotone_treatment_pulls_the_artwork_into_the_palette(stub_openai):
    art = await imagegen.generate(
        "Fog",
        ("#101A2C", "#EDE7DA", "#C9A44C", "#26364F"),
        target_w_px=200,
        target_h_px=300,
        model="openai",
        treatment="duotone",
        duotone_colours=("#101A2C", "#EDE7DA"),
    )
    assert art is not None
    colours = {art.image.getpixel((x, y)) for x in (0, 199) for y in (0, 299)}
    # Every pixel is on the ramp between the two palette ends.
    for r, g, b in colours:
        assert 0x10 <= r <= 0xED and 0x1A <= g <= 0xE7 and 0x2C <= b <= 0xDA


def test_duotone_maps_the_luminance_ends_onto_the_palette_ends():
    """Pins the exact mapping, so the ramp and ImageOps.colorize stay equivalent."""
    black = Image.new("RGB", (4, 4), (0, 0, 0))
    white = Image.new("RGB", (4, 4), (255, 255, 255))
    assert imagegen.duotone(black, "#102030", "#FFFFFF").getpixel((0, 0)) == (16, 32, 48)
    assert imagegen.duotone(white, "#102030", "#FFFFFF").getpixel((0, 0)) == (255, 255, 255)


def test_cover_crop_hits_the_target_exactly():
    out = imagegen.cover_crop(Image.new("RGB", (1024, 1536), "white"), 800, 1200)
    assert out.size == (800, 1200)


def test_prompt_guards_do_not_dictate_a_house_style():
    """The guards carry hard constraints only.

    Medium, colour and subject belong to the art director; baking a look in here
    is what made every cover come back flat and abstract.
    """
    prompt = build_prompt("A harbour town in gouache", ("#F6E3B6",), "illustrated")
    for banned in ("flat colour", "austere", "abstract", "matte finish"):
        assert banned not in prompt.lower(), f"guards still dictate {banned!r}"
    assert "no letters" in prompt
    assert "3D rendering" in prompt


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("illustrated", "mix freely"),
        ("painterly", "full tonal range"),
        ("typographic", "Restrict the palette"),
    ],
)
def test_palette_is_held_loosely_for_illustrated_covers(style, expected):
    prompt = build_prompt("A harbour town", ("#F6E3B6", "#D2662A"), style)
    assert expected in prompt
    assert "#F6E3B6" in prompt


async def test_missing_key_degrades_instead_of_raising(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    art = await imagegen.generate(
        "Fog", ("#000000",), target_w_px=100, target_h_px=100, model="openai"
    )
    assert isinstance(art, imagegen.Unavailable)


async def test_api_failure_degrades_instead_of_raising(monkeypatch):
    import openai

    class _Boom:
        class images:
            @staticmethod
            async def generate(**_kw):
                raise openai.APIConnectionError(request=None)  # type: ignore[arg-type]

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kw: _Boom())
    art = await imagegen.generate(
        "Fog", ("#000000",), target_w_px=100, target_h_px=100, model="openai"
    )
    assert isinstance(art, imagegen.Unavailable)


async def test_artwork_is_embedded_in_the_cover_and_reported(stub_openai, monkeypatch):
    from covers import render
    from covers.artdirection import ArtDirection

    # The PNG is rasterised from this SVG; catch it on the way through.
    fronts: list[str] = []
    build_front = render.build_front

    def spy(ctx):
        fronts.append(build_front(ctx))
        return fronts[-1]

    monkeypatch.setattr(render, "build_front", spy)

    result = await create_cover(
        image_model="openai",
        text="Ein Märchen aus dem Wald.",
        title="Der Wald",
        author="Brüder Grimm",
        format="rowohlt_hardcover",
        template="photo_duotone",
        palette="nachtblau",
        artwork="generated",
    )
    assert result["png"][:8] == b"\x89PNG\r\n\x1a\n"
    svg = fronts[-1]
    assert "<image" in svg
    assert "data:image/jpeg;base64," in svg

    meta = result["artwork"]
    assert meta is not None
    assert meta["provider"] == "openai"
    # Artwork keeps its own colour by default; duotone is opt-in because it
    # discards every hue the art direction asked for.
    assert meta["treatment"] == "none"

    # The reported resolution is the artwork's native height over the placement
    # it actually covers, not the requested output dpi.
    plan = artwork_plan(
        ArtDirection.model_validate(result["art_direction"]),
        geometry(FORMATS["rowohlt_hardcover"]),
    )
    assert plan is not None
    assert meta["placement_mm"][3] == pytest.approx(round(plan[3], 2), abs=0.01)
    assert meta["effective_dpi"] == imagegen.effective_dpi(1536, plan[3])


async def test_upscaling_is_disclosed_in_the_notes(stub_openai):
    result = await create_cover(
        image_model="openai",
        text="Ein Märchen.",
        title="Der Wald",
        author="Brüder Grimm",
        format="grossformat_hardcover",
        dpi=300,
        template="photo_duotone",
        artwork="generated",
    )
    # 1536 px cannot be 300 dpi across a 155 mm panel, and the note says so.
    assert any("dpi native" in n for n in result["notes"])


# --- Director selection ---


async def test_openai_director_produces_the_same_brief_shape(monkeypatch):
    """Either director yields an ArtDirection the renderer cannot distinguish."""
    from covers.artdirection import ArtDirection, fallback_direction
    from covers.director_openai import direct_openai

    captured: dict = {}
    reference = fallback_direction("Ein Haus am Hafen.", "Das Haus", "J. W.", "illustrated")

    class _Parsed:
        output_parsed = reference
        usage = type("U", (), {"input_tokens": 1200, "output_tokens": 400})()

    class _Responses:
        async def parse(self, **kw):
            captured.update(kw)
            return _Parsed()

    class _Client:
        responses = _Responses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kw: _Client())

    direction, meta = await direct_openai(
        "Ein Haus am Hafen.", "Das Haus", "J. W.", style="illustrated"
    )
    assert isinstance(direction, ArtDirection)
    assert meta["source"] == "openai"
    assert meta["usage"]["input_tokens"] == 1200
    # Same style guidance as the Claude director, same structured output target.
    assert captured["text_format"] is ArtDirection
    assert "ILLUSTRATED AND CHARMING" in captured["instructions"]

    # With a mood, the model is asked for the brief narrowed to that mood.
    from covers.artdirection import brief_schema
    from covers.moods import MOODS

    await direct_openai("Ein Mord.", "Nacht", "A. Autor", style="painterly", mood=MOODS["suspense"])
    assert captured["text_format"] is brief_schema(MOODS["suspense"])


async def test_openai_director_degrades_without_a_key(monkeypatch):
    from covers.director_openai import direct_openai

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    direction, meta = await direct_openai("x", "y", "z")
    assert meta["source"] == "fallback"
    assert direction.artwork in ("generated", "none")


@pytest.mark.parametrize("style", ["illustrated", "painterly", "typographic"])
def test_direct_path_builds_a_prompt_from_the_book_text(style):
    """director='none' runs no text model, so the prompt is composed locally."""
    from covers.director_openai import (
        _DIRECT_PREAMBLE,
        DIRECT_TEXT_CHARS,
        direct_prompt_from_text,
    )

    text = "Zwei Schwestern erben das Haus ihrer Großmutter am Hafen. " * 40
    prompt = direct_prompt_from_text(text, style)
    assert "Zwei Schwestern" in prompt
    assert len(prompt) < len(text)
    assert "\n" not in prompt  # whitespace normalised for the image model
    # The book text is bounded; only the register preamble is added to it.
    assert len(prompt) <= len(_DIRECT_PREAMBLE[style]) + DIRECT_TEXT_CHARS + 1


async def test_none_director_makes_no_text_model_call(stub_openai):
    result = await create_cover(
        image_model="openai",
        text="Zwei Schwestern erben das Haus ihrer Großmutter am Hafen.",
        title="Das Haus am Hafen", author="Jonas Wiechert", director="none", style="illustrated",
    )
    assert result["art_direction_meta"]["source"] == "none"
    assert result["director"] == "none"
    # The book's own words reached the image model.
    assert "Zwei Schwestern" in result["art_direction"]["image_prompt"]
    assert "Zwei Schwestern" in stub_openai["prompt"]


async def test_image_quality_is_forwarded_and_reported(stub_openai):
    result = await create_cover(
        image_model="openai",
        text="Ein Haus am Hafen.", title="Das Haus", author="J. W.", director="none", image_quality="low",
        artwork="generated", template="illustrated_full",
    )
    assert stub_openai["quality"] == "low"
    assert result["artwork"]["quality"] == "low"


# --- fal: Nano Banana Pro and FLUX.2 Pro ---


def _png(size=(512, 768), colour=(40, 60, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def stub_fal(monkeypatch):
    """Stand in for fal_client.AsyncClient and fal's CDN; record what they were asked."""
    import httpx

    recorder: dict = {}

    class _Client:
        def __init__(self, key=None, default_timeout=120.0):
            recorder["key"] = key

        async def subscribe(self, application, arguments, **kw):
            recorder.update(app=application, arguments=arguments, subscribe=kw)
            return {"images": [{"url": "https://fal.media/files/cover.jpg",
                                "description": "a door"}], "seed": 7}

    async def get(self, url, **kw):
        recorder["fetched"] = url
        return httpx.Response(200, content=_png(), request=httpx.Request("GET", url))

    monkeypatch.setenv("FAL_API_KEY", "fal-test-key")
    monkeypatch.setattr("fal_client.AsyncClient", _Client)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return recorder


def test_nano_banana_is_the_default_model():
    from covers.models import CoverRequest

    assert CoverRequest(text="x", title="x", author="x").image_model == "nano-banana-pro"


@pytest.mark.parametrize(("model", "app"), [
    ("nano-banana-pro", "fal-ai/nano-banana-pro"), ("flux-2-pro", "fal-ai/flux-2-pro"),
])
async def test_fal_models_paint_the_artwork(stub_fal, model, app):
    art = await imagegen.generate(
        "A red door in fog", ("#101A2C", "#EDE7DA"),
        target_w_px=400, target_h_px=600, quality="high", model=model,
    )
    assert art.image.size == (400, 600)  # cropped to the placement
    assert stub_fal["key"] == "fal-test-key"  # from FAL_API_KEY, passed explicitly
    assert stub_fal["app"] == app
    assert stub_fal["arguments"]["output_format"] == "jpeg"
    assert stub_fal["subscribe"]["interval"] >= 1.0  # not the client's 0.1 s polling
    assert stub_fal["fetched"] == "https://fal.media/files/cover.jpg"  # binary, from the CDN
    assert "no text" in stub_fal["arguments"]["prompt"].lower()  # lettering guards apply
    assert art.meta["provider"] == "fal" and art.meta["model"] == app
    assert art.meta["native_px"] == [512, 768]


async def test_nano_banana_gets_the_nearest_ratio_and_a_resolution(stub_fal):
    await imagegen.generate("x", ("#000000",), target_w_px=1594, target_h_px=2480,
                            quality="low", model="nano-banana-pro")
    assert stub_fal["arguments"]["aspect_ratio"] == "2:3"
    assert stub_fal["arguments"]["resolution"] == "1K"


async def test_flux_gets_the_placements_exact_aspect(stub_fal):
    await imagegen.generate("x", ("#000000",), target_w_px=1594, target_h_px=2480,
                            quality="high", model="flux-2-pro")
    size = stub_fal["arguments"]["image_size"]
    assert size["height"] == 2048 and size["width"] % 16 == 0
    assert abs(size["width"] / size["height"] - 1594 / 2480) < 0.01


async def test_fal_failure_says_why(stub_fal, monkeypatch):
    import fal_client

    async def boom(self, application, arguments, **kw):
        raise fal_client.FalClientHTTPError("boom", 500, {}, None)

    monkeypatch.setattr(fal_client.AsyncClient, "subscribe", boom, raising=False)
    art = await imagegen.generate("x", ("#000000",), target_w_px=100, target_h_px=150)
    assert isinstance(art, imagegen.Unavailable) and "boom" in art.reason


async def test_no_fal_key_says_so():
    art = await imagegen.generate("x", ("#000000",), target_w_px=100, target_h_px=150)
    assert art == imagegen.Unavailable("no FAL_API_KEY")


def test_every_image_model_has_a_spec():
    from typing import get_args

    assert set(imagegen.IMAGE_MODELS) == set(get_args(imagegen.ImageModel))


async def test_the_cover_note_names_the_missing_key():
    result = await create_cover(
        text="Ein Haus.", title="Das Haus", author="J. W.", director="none",
        artwork="generated", template="illustrated_full", image_model="flux-2-pro",
    )
    assert any("flux-2-pro" in n and "FAL_API_KEY" in n for n in result["notes"])
