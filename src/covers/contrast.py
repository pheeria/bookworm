"""Contrast between type and the picture under it, and toning the picture where it falls short.

Lengths are millimetres on the front canvas; a picture covers the artwork ``plan``
(x, y, w, h). A line of type is a ``Block``: upright, or turned about its centre.
``tones`` measures the pixels under a block; a ``Burn`` records a block whose type
does not read on them, and ``tone`` darkens or lightens the picture under each burn
-- as little as reaches its contrast, fading out around it -- so the type reads
with nothing put behind it.
"""

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .palettes import contrast_ratio, linear

Rect = tuple[float, float, float, float]

#: The long edge of the picture copy contrast is measured on.
SAMPLE_PX = 256
#: The share of the pixels under a line that its colour has to read on.
COVERED = 0.95


@dataclass(frozen=True)
class Block:
    """Where a line of type sits: its centre, size and the angle it is turned by (degrees)."""

    cx: float
    cy: float
    w: float
    h: float
    angle: float = 0.0

    @classmethod
    def upright(cls, x: float, y: float, w: float, h: float) -> "Block":
        return cls(x + w / 2, y + h / 2, w, h)

    def corners(self, grow: float = 0.0) -> list[tuple[float, float]]:
        t = math.radians(self.angle)
        hw, hh = self.w / 2 + grow, self.h / 2 + grow
        return [
            (self.cx + dx * math.cos(t) - dy * math.sin(t), self.cy + dx * math.sin(t) + dy * math.cos(t))
            for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
        ]

    def bbox(self, grow: float = 0.0) -> Rect:
        xs, ys = zip(*self.corners(grow), strict=True)
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


@dataclass(frozen=True)
class Burn:
    """Type of relative luminance ``ink`` that needs ``need`` contrast under ``block``."""

    block: Block
    ink: float
    need: float

    @property
    def light_type(self) -> bool:
        return self.ink > 0.5


def sample(img: Image.Image) -> Image.Image:
    """The small RGB copy of a picture that contrast is measured on."""
    return ImageOps.contain(img, (SAMPLE_PX, SAMPLE_PX)).convert("RGB")


def _px_box(rect: Rect, plan: Rect, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """``rect`` in the pixels of an image of ``size`` covering ``plan``; None if outside it."""
    px, py, pw, ph = plan
    x, y, w, h = rect
    sx, sy = size[0] / pw, size[1] / ph
    box = (
        max(0, int((x - px) * sx)), max(0, int((y - py) * sy)),
        min(size[0], math.ceil((x + w - px) * sx)), min(size[1], math.ceil((y + h - py) * sy)),
    )
    return box if box[2] > box[0] and box[3] > box[1] else None


def _mask(block: Block, plan: Rect, size: tuple[int, int], box: tuple[int, int, int, int], grow: float = 0.0):
    """The block, grown by ``grow``, as a mask over the ``box`` pixels of an image of ``size``."""
    sx, sy = size[0] / plan[2], size[1] / plan[3]
    corners = [((x - plan[0]) * sx - box[0], (y - plan[1]) * sy - box[1]) for x, y in block.corners(grow)]
    mask = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
    ImageDraw.Draw(mask).polygon(corners, fill=255)
    return mask


def _percentile(histogram: list[int], q: float) -> float:
    """The grey level below which a share ``q`` of the pixels fall, as relative luminance."""
    total, seen = sum(histogram), 0
    for level, count in enumerate(histogram):
        seen += count
        if seen >= q * total:
            return linear(level / 255)
    return 1.0


def median_tone(img: Image.Image) -> float:
    """The picture's middle tone, as relative luminance."""
    return _percentile(img.convert("L").histogram(), 0.5)


def tones(img: Image.Image, block: Block, plan: Rect) -> tuple[float, float] | None:
    """How dark and how light ``img`` (covering ``plan``) is under ``block``: the
    relative luminance at either end of the pixels a line has to read on. None
    outside the picture."""
    box = _px_box(block.bbox(), plan, img.size)
    if box is None:
        return None
    crop = img.crop(box).convert("L")
    histogram = crop.histogram(_mask(block, plan, img.size, box)) if block.angle else crop.histogram()
    if not sum(histogram):
        histogram = crop.histogram()
    return _percentile(histogram, 1 - COVERED), _percentile(histogram, COVERED)


def _toned(img: Image.Image, light_type: bool, strength: float) -> Image.Image:
    """Darkened under light type, lightened under dark type, by ``strength`` (0 to 1)."""
    if light_type:
        return ImageEnhance.Brightness(img).enhance(1 - strength)
    return Image.blend(img, Image.new("RGB", img.size, "white"), strength)


def _least_toning(small: Image.Image, burn: Burn, plan: Rect) -> float:
    """The least toning of ``small`` at which the burn's type reads; 0 if it does already."""

    def reads(strength: float) -> bool:
        found = tones(_toned(small, burn.light_type, strength), burn.block, plan)
        # A tenth over, for what resampling to and from the small copy loses.
        return found is None or min(contrast_ratio(burn.ink, t) for t in found) >= burn.need * 1.1

    if reads(0.0):
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(10):
        mid = (low + high) / 2
        low, high = (low, mid) if reads(mid) else (mid, high)
    return high


def tone(img: Image.Image, burns: list[Burn], plan: Rect, feather: float) -> Image.Image:
    """``img`` toned under each burn: at full strength over its block, fading out over
    ``feather`` (mm) beyond it. Neighbours' fades can overlap, so every burn is
    measured again afterwards and topped up where it slipped."""
    img = img.convert("RGB")
    small = sample(img)
    # Each burn's soft mask, at full size and at the size it is measured at.
    masks = []
    for burn in burns:
        layers = []
        for size in (img.size, small.size):
            box = _px_box(burn.block.bbox(2 * feather), plan, size)
            if box is not None:
                blur = feather * size[0] / plan[2] / 2
                layers.append((box, _mask(burn.block, plan, size, box, feather).filter(ImageFilter.GaussianBlur(blur))))
        masks.append(layers if len(layers) == 2 else None)

    for _ in range(3):
        toned_any = False
        for burn, layers in zip(burns, masks, strict=True):
            if layers is None or not (strength := _least_toning(small, burn, plan)):
                continue
            for picture, (box, mask) in zip((img, small), layers, strict=True):
                region = picture.crop(box)
                picture.paste(Image.composite(_toned(region, burn.light_type, strength), region, mask), box[:2])
            toned_any = True
        if not toned_any:
            break
    return img
