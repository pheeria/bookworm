"""Orchestration: prompt in, print-ready cover out.

    text/title/author
        -> Claude writes the art direction and the image prompt
        -> OpenAI paints the artwork (optional)
        -> the typography engine sets the type in German trade geometry
        -> SVG / PNG / JPEG / PDF
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from . import imagegen
from .artdirection import DEFAULT_STYLE, apply_overrides, direct
from .formats import geometry, px, resolve_format
from .layout import Content, Ctx, artwork_plan
from .palettes import luminance
from .render import DEFAULT_ASSETS, render

log = logging.getLogger("bookworm.pipeline")

#: Motif used when generated artwork was asked for but could not be produced.
_MOTIF_FALLBACK = ("arcs", "blocks", "dots", "split", "waveform", "rings")


def _seed(*parts: str) -> int:
    digest = hashlib.blake2b("\x1f".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _duotone_pair(hexes: tuple[str, ...]) -> tuple[str, str]:
    """Darkest and lightest of the palette, so the artwork stays in register."""
    ordered = sorted(hexes, key=luminance)
    return ordered[0], ordered[-1]


async def create_cover(
    *,
    text: str,
    title: str,
    author: str,
    outdir: Path,
    format_key: str | None = None,
    pages: int = 288,
    dpi: int = 300,
    imprint: str | None = None,
    isbn: str = "",
    price: str = "",
    translator: str = "",
    template: str | None = None,
    type_family: str | None = None,
    palette: str | None = None,
    artwork: str | None = None,
    motif: str | None = None,
    genre_line: str | None = None,
    blurb: str | None = None,
    style: str = DEFAULT_STYLE,
    treatment: str = "none",
    seed: int | None = None,
    marks: bool = False,
    spine_direction: str = "top_to_bottom",
    assets: tuple[str, ...] = DEFAULT_ASSETS,
) -> dict:
    fmt = resolve_format(format_key)
    geo = geometry(fmt, pages=pages, dpi=dpi)

    direction, ad_meta = await direct(text, title, author, style=style)
    direction = apply_overrides(
        direction,
        template=template,
        type_family=type_family,
        palette_key=palette,
        artwork=artwork,
        motif=motif,
        genre_line=genre_line,
    )
    # Asking for a motif implies you want it drawn, even if the brief said the
    # cover should be purely typographic.
    if motif and motif != "none" and artwork is None and direction.artwork == "none":
        direction = apply_overrides(direction, artwork="procedural")

    if seed is None:
        seed = _seed(text, title, author, direction.template, direction.ground)

    notes: list[str] = []
    artwork_uri = None
    art_meta: dict | None = None

    plan = artwork_plan(direction, geo)
    if direction.artwork == "generated" and plan is None:
        notes.append(
            f"template {direction.template!r} is typographic; generated artwork skipped"
        )
    elif direction.artwork == "generated" and plan is not None:
        hexes = (direction.ground, direction.ink, direction.accent, direction.secondary)
        art = await imagegen.generate(
            direction.image_prompt,
            hexes,
            target_w_px=px(plan[2], dpi),
            target_h_px=px(plan[3], dpi),
            treatment=treatment,  # type: ignore[arg-type]
            duotone_colours=_duotone_pair(hexes),
            style=style,
        )
        if art is None:
            notes.append(
                "image generation unavailable (no OPENAI_API_KEY or the call failed); "
                "used a procedural motif instead"
            )
            if direction.motif == "none":
                direction = apply_overrides(
                    direction, motif=_MOTIF_FALLBACK[seed % len(_MOTIF_FALLBACK)]
                )
        else:
            artwork_uri = imagegen.to_data_uri(art.image)
            art_meta = dict(art.meta)
            art_meta["placement_mm"] = [round(v, 2) for v in plan]
            art_meta["effective_dpi"] = imagegen.effective_dpi(
                art.meta["native_px"][1], plan[3]
            )
            if art_meta["effective_dpi"] < dpi:
                notes.append(
                    f"artwork is {art_meta['effective_dpi']} dpi native over its "
                    f"placement and was resampled up to {dpi} dpi; type stays vector"
                )

    content = Content(
        title=title,
        author=author,
        genre_line=direction.genre_line,
        blurb=blurb if blurb is not None else direction.blurb,
        imprint=imprint if imprint is not None else (
            fmt.imprint if fmt.imprint != "allgemein" else ""
        ),
        isbn=isbn,
        price=price,
        translator=translator,
    )

    ctx = Ctx(
        geo=geo,
        direction=direction,
        content=content,
        palette=direction.palette,
        seed=seed,
        artwork_uri=artwork_uri,
        marks=marks,
        spine_direction=spine_direction,
        notes=notes,
    )

    result = await run_in_threadpool(render, ctx, outdir, assets)

    return {
        "art_direction": direction.model_dump(),
        "art_direction_meta": ad_meta,
        "artwork": art_meta,
        "geometry": geo.to_dict(),
        "content": {
            "title": content.title,
            "author": content.author,
            "genre_line": content.genre_line,
            "blurb": content.blurb,
            "imprint": content.imprint,
        },
        "style": style,
        "seed": seed,
        "files": {k: str(v) for k, v in result.files.items()},
        "notes": result.notes,
    }
