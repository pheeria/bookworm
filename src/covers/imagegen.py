"""Cover artwork from an image model: Nano Banana Pro or FLUX.2 Pro on fal, or OpenAI.

The image model paints the artwork only. All lettering is set afterwards as vector
type by :mod:`covers.layout`, because generated type is never printable -- so the
prompt is hardened against lettering before it is sent.

Artwork is generated for the front panel, cover-cropped to the panel's bleed box,
and resampled to the requested resolution. The response reports the artwork's
native resolution so nobody mistakes an upscale for real 300 dpi detail.
"""

import asyncio
import base64
import io
import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, get_args

from PIL import Image, ImageOps

from . import settings
from .artdirection import DEFAULT_STYLE
from .formats import MM_PER_INCH
from .palettes import rgb

log = logging.getLogger("covers.imagegen")

Quality = Literal["low", "medium", "high", "auto"]

#: The image models a cover can be painted with. ``openai`` is whichever model
#: COVERS_OPENAI_IMAGE_MODEL names (gpt-image-2 by default).
ImageModel = Literal["nano-banana-pro", "flux-2-pro", "openai"]
DEFAULT_IMAGE_MODEL: ImageModel = "nano-banana-pro"


def _nearest(options: tuple[tuple[str, float], ...], aspect: float) -> str:
    """The option whose aspect (w/h) is closest to ``aspect``, on a log scale."""
    return min(options, key=lambda o: abs(math.log(o[1]) - math.log(aspect)))[0]


def _high(quality: str) -> bool:
    return quality in ("high", "auto")


#: Sizes gpt-image models accept, with their aspect ratios (w/h).
_OPENAI_SIZES = (("1024x1536", 1024 / 1536), ("1024x1024", 1.0), ("1536x1024", 1536 / 1024))
#: Aspect ratios Nano Banana Pro accepts.
_NANO_RATIOS = tuple(
    (r, int(r.split(":")[0]) / int(r.split(":")[1]))
    for r in ("21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16")
)


def _nano_arguments(aspect: float, quality: str) -> dict:
    """A fixed ratio and a resolution tier."""
    return {
        "aspect_ratio": _nearest(_NANO_RATIOS, aspect),
        "resolution": "2K" if _high(quality) else "1K",
        "num_images": 1,
    }


def _flux_arguments(aspect: float, quality: str) -> dict:
    """A custom size at the placement's exact aspect, in multiples of 16."""
    long_edge = 2048 if _high(quality) else 1024
    w, h = (long_edge, long_edge / aspect) if aspect >= 1 else (long_edge * aspect, long_edge)
    return {"image_size": {"width": round(w / 16) * 16, "height": round(h / 16) * 16}}


@dataclass(frozen=True)
class ModelSpec:
    """Everything that differs between image models."""

    key_env: str
    #: fal application id; None for OpenAI, which has its own client.
    app: str | None = None
    #: fal only: the size (and other model-specific) arguments for an aspect and quality.
    arguments: Callable[[float, str], dict] | None = None


IMAGE_MODELS: dict[str, ModelSpec] = {
    "nano-banana-pro": ModelSpec("FAL_API_KEY", "fal-ai/nano-banana-pro", _nano_arguments),
    "flux-2-pro": ModelSpec("FAL_API_KEY", "fal-ai/flux-2-pro", _flux_arguments),
    "openai": ModelSpec("OPENAI_API_KEY"),
}
assert set(IMAGE_MODELS) == set(get_args(ImageModel)), "ImageModel and IMAGE_MODELS disagree"


@dataclass(frozen=True)
class Unavailable:
    """No artwork, and why: the cover falls back to a procedural motif and says so."""

    reason: str


Treatment = Literal["none", "duotone", "grayscale"]

#: Hard constraints only. Anything about medium, colour or subject belongs in the
#: art director's prompt -- baking a house style in here is how every cover ends up
#: looking the same.
_PROMPT_GUARDS = (
    "Absolutely no text, no letters, no words, no numbers, no signatures, no logos "
    "and no book-cover mockup: this is artwork only, and all typography is added "
    "later. Fill the entire frame edge to edge with no border, no frame and no "
    "margin. Avoid 3D rendering and stock-photo styling."
)

#: How tightly the artwork is held to the cover palette, per register.
_PALETTE_INSTRUCTION = {
    "illustrated": (
        "Let these colours lead the scheme, but mix freely around them -- a hand-"
        "mixed illustration palette, not a restricted one: {colours}."
    ),
    "painterly": (
        "Build the painting's colour around these, with the full tonal range they "
        "imply: {colours}."
    ),
    "typographic": (
        "Restrict the palette to these colours and close neighbours of them: "
        "{colours}."
    ),
}


@dataclass
class Artwork:
    image: Image.Image
    meta: dict = field(default_factory=dict)


def build_prompt(
    image_prompt: str,
    palette_hexes: tuple[str, ...],
    style: str = DEFAULT_STYLE,
) -> str:
    """Harden the art director's prompt before it reaches the image model."""
    colours = ", ".join(palette_hexes)
    palette_rule = _PALETTE_INSTRUCTION.get(
        style, _PALETTE_INSTRUCTION[DEFAULT_STYLE]
    ).format(colours=colours)
    return f"{image_prompt.strip()}\n\n{palette_rule} {_PROMPT_GUARDS}"


async def generate(
    image_prompt: str,
    palette_hexes: tuple[str, ...],
    *,
    target_w_px: int,
    target_h_px: int,
    treatment: Treatment = "none",
    duotone_colours: tuple[str, str] | None = None,
    style: str = DEFAULT_STYLE,
    quality: str | None = None,
    model: ImageModel = DEFAULT_IMAGE_MODEL,
) -> Artwork | Unavailable:
    """Paint the front-cover artwork, or say why it could not be painted.

    Degrading rather than raising lets the renderer fall back to a procedural
    motif, so a missing key costs the cover its picture, not the request.
    """
    spec = IMAGE_MODELS[model]
    if not (key := os.environ.get(spec.key_env)):
        return Unavailable(f"no {spec.key_env}")
    quality = quality or settings.image_quality()
    prompt = build_prompt(image_prompt, palette_hexes, style)
    aspect = target_w_px / target_h_px
    painted = await (
        _openai(prompt, aspect, quality) if spec.app is None
        else _fal(spec, key, prompt, aspect, quality)
    )
    if isinstance(painted, Unavailable):
        log.warning("image generation with %s unavailable: %s", model, painted.reason)
        return painted
    data, meta = painted

    # Decoding, resampling and toning a multi-megapixel image is CPU-bound.
    try:
        art, (native_w, native_h) = await asyncio.to_thread(
            _process, data, target_w_px, target_h_px, treatment, duotone_colours
        )
    except (OSError, ValueError) as exc:  # not an image after all
        log.warning("image from %s could not be decoded: %s", model, exc)
        return Unavailable(f"{model} returned an unreadable image")
    return Artwork(
        image=art,
        meta={
            **meta,
            "quality": quality,
            "native_px": [native_w, native_h],
            "treatment": treatment,
            "prompt": prompt,
        },
    )


async def _openai(prompt: str, aspect: float, quality: str) -> tuple[str, dict] | Unavailable:
    import openai

    model, size = settings.openai_image_model(), _nearest(_OPENAI_SIZES, aspect)
    # No retries: a slow render retried would be paid for, and waited for, again.
    client = openai.AsyncOpenAI(timeout=180.0, max_retries=0)
    try:
        response = await client.images.generate(
            model=model, prompt=prompt, size=size, quality=quality, output_format="png", n=1,
        )
    except (TimeoutError, openai.OpenAIError) as exc:
        return Unavailable(f"{model} failed: {exc}")

    datum = response.data[0] if response.data else None
    if datum is None or not datum.b64_json:
        return Unavailable(f"{model} returned no image")
    # Base64, decoded in the worker thread with the rest of the processing.
    return datum.b64_json, {
        "provider": "openai",
        "model": model,
        "requested_size": size,
        "revised_prompt": getattr(datum, "revised_prompt", None),
    }


async def _fal(
    spec: ModelSpec, key: str, prompt: str, aspect: float, quality: str
) -> tuple[bytes | str, dict] | Unavailable:
    import fal_client
    import httpx

    size = spec.arguments(aspect, quality)
    # JPEG: the artwork is resampled and embedded as JPEG anyway, and it downloads
    # several times smaller than PNG. The binary comes from fal's CDN rather than
    # inline as base64 (sync_mode), which would inflate it by a third inside JSON.
    arguments = {"prompt": prompt, "output_format": "jpeg", **size}
    # FAL_API_KEY is ours; the client on its own would only look for FAL_KEY.
    client = fal_client.AsyncClient(key=key, default_timeout=180.0)
    try:
        # A render takes tens of seconds; polling the queue every 0.1 s (the
        # client's default) would be hundreds of status calls per cover.
        result = await client.subscribe(
            spec.app, arguments=arguments, interval=1.0, client_timeout=180.0
        )
        image = result["images"][0]
        data = await _fetch(image["url"])
    except (fal_client.FalClientError, httpx.HTTPError, LookupError, TypeError, ValueError) as exc:
        return Unavailable(f"{spec.app} failed: {exc}")
    return data, {
        "provider": "fal",
        "model": spec.app,
        "requested_size": size,
        "revised_prompt": image.get("description"),
        "seed": result.get("seed"),
    }


async def _fetch(url: str) -> bytes | str:
    """The image behind a fal result: downloaded from the CDN, or the base64 of a
    data URI, which ``_process`` decodes in its worker thread."""
    if url.startswith("data:"):
        return url.partition(",")[2]
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.get(url)
        response.raise_for_status()
        return response.content


def _process(
    data: bytes | str,
    target_w: int,
    target_h: int,
    treatment: Treatment,
    duotone_colours: tuple[str, str] | None,
) -> tuple[Image.Image, tuple[int, int]]:
    """Decode, crop and tone the artwork. Returns it and its native size.

    ``data`` is the image's bytes, or its base64 as the provider sent it.
    """
    if isinstance(data, str):
        data = base64.b64decode(data)
    native = Image.open(io.BytesIO(data)).convert("RGB")
    art = cover_crop(native, target_w, target_h)
    if treatment == "grayscale":
        art = art.convert("L").convert("RGB")
    elif treatment == "duotone" and duotone_colours:
        art = duotone(art, *duotone_colours)
    return art, native.size


def cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale and centre-crop to exactly ``target_w`` x ``target_h`` without distortion."""
    return ImageOps.fit(img, (target_w, target_h), Image.LANCZOS)


def duotone(img: Image.Image, shadow_hex: str, highlight_hex: str) -> Image.Image:
    """Map luminance onto two palette colours, which holds the cover together."""
    return ImageOps.colorize(img.convert("L"), rgb(shadow_hex), rgb(highlight_hex))


def to_data_uri(img: Image.Image, *, quality: int = 92) -> str:
    """Encode for embedding in the SVG. JPEG, because artwork is continuous-tone."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=1)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def effective_dpi(native_px: int, extent_mm: float) -> int:
    """True resolution of the artwork over the panel it covers."""
    if extent_mm <= 0:
        return 0
    return round(native_px / (extent_mm / MM_PER_INCH))
