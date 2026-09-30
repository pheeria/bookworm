"""The asset catalogue: what each output is called, and what it is.

Pure data. It lives apart from the renderer so that validating a request does not
require libcairo to be loadable — `models.py` needs the asset *names*, not the
machinery that produces them.

Three facts about each asset used to live in three files: the name in `render.ASSETS`,
the filename inline in `render()`, and the media type in the router's `_MEDIA` table.
"""

from typing import Literal, NamedTuple

Surface = Literal["front", "spread", "meta"]
Format = Literal["png", "jpeg", "svg", "pdf", "json"]


class Asset(NamedTuple):
    filename: str
    media_type: str
    #: Which document it is rendered from, or "meta" for the sidecar record.
    surface: Surface
    format: Format


ASSETS: dict[str, Asset] = {
    "front_png": Asset("front.png", "image/png", "front", "png"),
    "front_jpg": Asset("front.jpg", "image/jpeg", "front", "jpeg"),
    "front_svg": Asset("front.svg", "image/svg+xml", "front", "svg"),
    "front_pdf": Asset("front.pdf", "application/pdf", "front", "pdf"),
    "spread_svg": Asset("spread.svg", "image/svg+xml", "spread", "svg"),
    "spread_pdf": Asset("spread.pdf", "application/pdf", "spread", "pdf"),
    "spread_png": Asset("spread.png", "image/png", "spread", "png"),
    "direction_json": Asset("direction.json", "application/json", "meta", "json"),
}

ASSET_NAMES: tuple[str, ...] = tuple(ASSETS)
DEFAULT_ASSETS: tuple[str, ...] = (
    "front_png",
    "front_jpg",
    "front_svg",
    "spread_pdf",
    "direction_json",
)

MEDIA_BY_SUFFIX = {
    f".{a.filename.rsplit('.', 1)[1]}": a.media_type for a in ASSETS.values()
}


def needs(assets: tuple[str, ...], surface: Surface) -> bool:
    """Whether any requested asset is rendered from ``surface``."""
    return any(ASSETS[a].surface == surface for a in assets if a in ASSETS)
