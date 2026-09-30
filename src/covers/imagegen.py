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
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageOps

from . import settings
from .artdirection import DEFAULT_STYLE
from .formats import MM_PER_INCH
from .palettes import rgb

log = logging.getLogger("covers.imagegen")

Quality = Literal["low", "medium", "high", "auto"]

#: The image models a cover can be painted with. ``openai`` is whichever model
#: COVERS_IMAGE_MODEL names (gpt-image-2 by default).
ImageModel = Literal["nano-banana-pro", "flux-2-pro", "openai"]
DEFAULT_IMAGE_MODEL: ImageModel = "nano-banana-pro"

#: fal application ids.
FAL_APPS = {"nano-banana-pro": "fal-ai/nano-banana-pro", "flux-2-pro": "fal-ai/flux-2-pro"}

#: Aspect ratios Nano Banana Pro accepts.
_NANO_RATIOS = ("21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16")


def key_for(model: str) -> str:
    """The environment variable an image model needs."""
    return "OPENAI_API_KEY" if model == "openai" else "FAL_API_KEY"

#: Sizes gpt-image models accept, with their aspect ratios (w/h).
_SIZES: tuple[tuple[str, float], ...] = (
    ("1024x1536", 1024 / 1536),
    ("1024x1024", 1.0),
    ("1536x1024", 1536 / 1024),
)

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


def _best_size(aspect_w_over_h: float) -> str:
    return min(_SIZES, key=lambda s: abs(s[1] - aspect_w_over_h))[0]


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
) -> Artwork | None:
    """Paint the front-cover artwork, or return ``None`` if the model is unavailable.

    Returning ``None`` rather than raising lets the renderer fall back to a
    procedural motif, so a missing key degrades the cover instead of the request.
    """
    quality = quality or settings.image_quality()
    prompt = build_prompt(image_prompt, palette_hexes, style)
    aspect = target_w_px / target_h_px
    painted = (
        await _openai(prompt, aspect, quality) if model == "openai"
        else await _fal(model, prompt, aspect, quality)
    )
    if painted is None:
        return None
    data, meta = painted

    # Decoding, resampling and toning a multi-megapixel image is CPU-bound.
    art, (native_w, native_h) = await asyncio.to_thread(
        _process, data, target_w_px, target_h_px, treatment, duotone_colours
    )
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


async def _openai(prompt: str, aspect: float, quality: str) -> tuple[bytes, dict] | None:
    if not os.environ.get("OPENAI_API_KEY"):
        log.info("OPENAI_API_KEY not set; skipping artwork")
        return None

    import openai

    model, size = settings.image_model(), _best_size(aspect)
    client = openai.AsyncOpenAI(timeout=180.0)
    try:
        response = await client.images.generate(
            model=model, prompt=prompt, size=size, quality=quality, output_format="png", n=1,
        )
    except (TimeoutError, openai.OpenAIError) as exc:
        log.warning("image generation failed (%s); falling back to a motif", exc)
        return None

    datum = response.data[0] if response.data else None
    if datum is None or not datum.b64_json:
        log.warning("image generation returned no image data; falling back to a motif")
        return None
    return base64.b64decode(datum.b64_json), {
        "provider": "openai",
        "model": model,
        "requested_size": size,
        "revised_prompt": getattr(datum, "revised_prompt", None),
    }


def fal_size(model: str, aspect: float, quality: str) -> dict:
    """The size arguments a fal model takes, for artwork of this aspect (w/h).

    Nano Banana Pro takes a fixed set of ratios and a resolution tier; FLUX.2 Pro a
    custom size, which can match the placement exactly.
    """
    high = quality in ("high", "auto")
    if model == "nano-banana-pro":
        def distance(ratio: str) -> float:
            w, h = ratio.split(":")
            return abs(math.log(int(w) / int(h)) - math.log(aspect))
        return {"aspect_ratio": min(_NANO_RATIOS, key=distance), "resolution": "2K" if high else "1K"}
    long_edge = 2048 if high else 1024
    w, h = (long_edge, long_edge / aspect) if aspect >= 1 else (long_edge * aspect, long_edge)
    return {"image_size": {"width": round(w / 16) * 16, "height": round(h / 16) * 16}}


async def _fal(model: str, prompt: str, aspect: float, quality: str) -> tuple[bytes, dict] | None:
    if not (key := os.environ.get("FAL_API_KEY")):
        log.info("FAL_API_KEY not set; skipping artwork")
        return None

    import fal_client
    import httpx

    app, size = FAL_APPS[model], fal_size(model, aspect, quality)
    arguments = {"prompt": prompt, "output_format": "png", "sync_mode": True, **size}
    if model == "nano-banana-pro":
        arguments["num_images"] = 1
    # FAL_API_KEY is ours; the client on its own would only look for FAL_KEY.
    client = fal_client.AsyncClient(key=key, default_timeout=180.0)
    try:
        result = await client.subscribe(app, arguments=arguments, client_timeout=180.0)
        image = result["images"][0]
        data = await _fetch(image["url"])
    except (fal_client.FalClientError, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("image generation on %s failed (%s); falling back to a motif", app, exc)
        return None
    return data, {
        "provider": "fal",
        "model": app,
        "requested_size": size,
        "revised_prompt": image.get("description"),
        "seed": result.get("seed"),
    }


async def _fetch(url: str) -> bytes:
    """The image behind a fal result: inline with sync_mode, else on fal's CDN."""
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.get(url)
        response.raise_for_status()
        return response.content


def _process(
    data: bytes,
    target_w: int,
    target_h: int,
    treatment: Treatment,
    duotone_colours: tuple[str, str] | None,
) -> tuple[Image.Image, tuple[int, int]]:
    """Decode, crop and tone the artwork. Returns it and its native size."""
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
