"""The OpenAI artwork path, exercised against a stub client.

These tests cover the plumbing around the call -- prompt hardening, cropping to
the planned placement, the duotone treatment, embedding, and the effective-dpi
report. They do not call OpenAI; a live call needs OPENAI_API_KEY.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from bookworm import imagegen
from bookworm.formats import FORMATS, geometry
from bookworm.imagegen import build_prompt
from bookworm.layout import artwork_plan
from bookworm.pipeline import create_cover


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
        "Fog", ("#000000",), target_w_px=100, target_h_px=100
    )
    assert art is None


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
        "Fog", ("#000000",), target_w_px=100, target_h_px=100
    )
    assert art is None


async def test_artwork_is_embedded_in_the_cover_and_reported(tmp_path, stub_openai):
    from bookworm.artdirection import ArtDirection

    result = await create_cover(
        text="Ein Märchen aus dem Wald.",
        title="Der Wald",
        author="Brüder Grimm",
        outdir=tmp_path,
        format_key="rowohlt_hardcover",
        pages=464,
        template="photo_duotone",
        palette="nachtblau",
        artwork="generated",
        assets=("front_svg", "front_png"),
    )
    svg = (tmp_path / "front.svg").read_text(encoding="utf-8")
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
        geometry(FORMATS["rowohlt_hardcover"], pages=464),
    )
    assert plan is not None
    assert meta["placement_mm"][3] == pytest.approx(round(plan[3], 2), abs=0.01)
    assert meta["effective_dpi"] == imagegen.effective_dpi(1536, plan[3])


async def test_upscaling_is_disclosed_in_the_notes(tmp_path, stub_openai):
    result = await create_cover(
        text="Ein Märchen.",
        title="Der Wald",
        author="Brüder Grimm",
        outdir=tmp_path,
        format_key="grossformat_hardcover",
        pages=500,
        dpi=300,
        template="photo_duotone",
        artwork="generated",
        assets=("front_svg",),
    )
    # 1536 px cannot be 300 dpi across a 155 mm panel, and the note says so.
    assert any("dpi native" in n for n in result["notes"])


# --- Director selection ---


async def test_openai_director_produces_the_same_brief_shape(monkeypatch):
    """Either director yields an ArtDirection the renderer cannot distinguish."""
    from bookworm.artdirection import ArtDirection, fallback_direction
    from bookworm.director_openai import direct_openai

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


async def test_openai_director_degrades_without_a_key(monkeypatch):
    from bookworm.director_openai import direct_openai

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    direction, meta = await direct_openai("x", "y", "z")
    assert meta["source"] == "fallback"
    assert direction.artwork in ("generated", "none")


@pytest.mark.parametrize("style", ["illustrated", "painterly", "typographic"])
def test_direct_path_builds_a_prompt_from_the_book_text(style):
    """director='none' runs no text model, so the prompt is composed locally."""
    from bookworm.director_openai import (
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


async def test_none_director_makes_no_text_model_call(tmp_path, stub_openai):
    result = await create_cover(
        text="Zwei Schwestern erben das Haus ihrer Großmutter am Hafen.",
        title="Das Haus am Hafen", author="Jonas Wiechert",
        outdir=tmp_path, director="none", style="illustrated",
        assets=("front_svg",),
    )
    assert result["art_direction_meta"]["source"] == "none"
    assert result["director"] == "none"
    # The book's own words reached the image model.
    assert "Zwei Schwestern" in result["art_direction"]["image_prompt"]
    assert "Zwei Schwestern" in stub_openai["prompt"]


async def test_image_quality_is_forwarded_and_reported(tmp_path, stub_openai):
    result = await create_cover(
        text="Ein Haus am Hafen.", title="Das Haus", author="J. W.",
        outdir=tmp_path, director="none", image_quality="low",
        artwork="generated", template="illustrated_full", assets=("front_svg",),
    )
    assert stub_openai["quality"] == "low"
    assert result["artwork"]["quality"] == "low"
