"""Cover artwork via OpenAI's image models.

The image model paints the artwork only. All lettering is set afterwards as vector
type by :mod:`bookworm.layout`, because generated type is never printable -- so the
prompt is hardened against lettering before it is sent.

Artwork is generated for the front panel, cover-cropped to the panel's bleed box,
and resampled to the requested resolution. The response reports the artwork's
native resolution so nobody mistakes an upscale for real 300 dpi detail.
"""

import base64
import io
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageOps

from .artdirection import DEFAULT_STYLE
from .formats import MM_PER_INCH
from .palettes import rgb

log = logging.getLogger("bookworm.imagegen")

MODEL = os.environ.get("BOOKWORM_IMAGE_MODEL", "gpt-image-2")
QUALITY = os.environ.get("BOOKWORM_IMAGE_QUALITY", "high")

Quality = Literal["low", "medium", "high", "auto"]

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
    model: str | None = None,
    quality: str | None = None,
    timeout: float = 180.0,
) -> Artwork | None:
    """Paint the front-cover artwork, or return ``None`` if OpenAI is unavailable.

    Returning ``None`` rather than raising lets the renderer fall back to a
    procedural motif, so a missing key degrades the cover instead of the request.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:  # pragma: no cover
        log.info("openai package not installed; skipping artwork")
        return None

    if not os.environ.get("OPENAI_API_KEY"):
        log.info("OPENAI_API_KEY not set; skipping artwork")
        return None

    import openai

    model = model or MODEL
    quality = quality or QUALITY
    size = _best_size(target_w_px / target_h_px)
    prompt = build_prompt(image_prompt, palette_hexes, style)

    client = AsyncOpenAI(timeout=timeout)
    try:
        response = await client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
            output_format="png",
            n=1,
        )
    except (TimeoutError, openai.OpenAIError) as exc:
        log.warning("image generation failed (%s); falling back to a motif", exc)
        return None

    datum = response.data[0] if response.data else None
    if datum is None or not datum.b64_json:
        log.warning("image generation returned no image data; falling back to a motif")
        return None

    raw = base64.b64decode(datum.b64_json)
    native = Image.open(io.BytesIO(raw)).convert("RGB")
    native_w, native_h = native.size

    art = cover_crop(native, target_w_px, target_h_px)
    if treatment == "grayscale":
        art = art.convert("L").convert("RGB")
    elif treatment == "duotone" and duotone_colours:
        art = duotone(art, *duotone_colours)

    return Artwork(
        image=art,
        meta={
            "provider": "openai",
            "model": model,
            "quality": quality,
            "requested_size": size,
            "native_px": [native_w, native_h],
            "treatment": treatment,
            "revised_prompt": getattr(datum, "revised_prompt", None),
            "prompt": prompt,
        },
    )


def cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale and centre-crop to exactly ``target_w`` x ``target_h`` without distortion."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new = (max(1, round(src_w * scale)), max(1, round(src_h * scale)))
    resized = img.resize(new, Image.LANCZOS)
    left = (new[0] - target_w) // 2
    top = (new[1] - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def duotone(img: Image.Image, shadow_hex: str, highlight_hex: str) -> Image.Image:
    """Map luminance onto two palette colours, which holds the cover together."""
    return ImageOps.colorize(img.convert("L"), rgb(shadow_hex), rgb(highlight_hex))


def to_data_uri(img: Image.Image, *, quality: int = 92) -> str:
    """Encode for embedding in the SVG. JPEG, because artwork is continuous-tone."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=1, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def effective_dpi(native_px: int, extent_mm: float) -> int:
    """True resolution of the artwork over the panel it covers."""
    if extent_mm <= 0:
        return 0
    return round(native_px / (extent_mm / MM_PER_INCH))
