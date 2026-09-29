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
        self.url = None
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
