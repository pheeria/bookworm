"""Orchestration: prompt in, print-ready front cover out.

    text/title/author
        -> Claude writes the art direction and the image prompt
        -> OpenAI paints the artwork (optional)
        -> the typography engine sets the type in German trade geometry
        -> front.png
"""

import asyncio
import logging
from pathlib import Path

from . import imagegen
from .artdirection import (
    DEFAULT_STYLE,
    DRAWN_MOTIFS,
    apply_overrides,
    direct,
    fallback_direction,
    seed_from,
)
from .director_openai import direct_openai, direct_prompt_from_text
from .formats import geometry, px, resolve_format
from .layout import Content, Ctx, artwork_plan
from .palettes import darkest_and_lightest
from .render import render

log = logging.getLogger("covers.pipeline")

async def create_cover(
    *,
    text: str,
    title: str,
    author: str,
    outdir: Path,
    format_key: str | None = None,
    dpi: int = 300,
    imprint: str | None = None,
    template: str | None = None,
    type_family: str | None = None,
    palette: str | None = None,
    artwork: str | None = None,
    motif: str | None = None,
    genre_line: str | None = None,
    style: str = DEFAULT_STYLE,
    director: str = "claude",
    treatment: str = "none",
    image_quality: str | None = None,
    seed: int | None = None,
    marks: bool = False,
) -> dict:
    fmt = resolve_format(format_key)
    geo = geometry(fmt, dpi=dpi)

    if director == "openai":
        direction, ad_meta = await direct_openai(text, title, author, style=style)
    elif director == "none":
        # No text model at all: the deterministic brief decides everything except
        # the picture, whose prompt is composed locally from the book's own words.
        direction = fallback_direction(text, title, author, style)
        direction = direction.model_copy(
            update={"image_prompt": direct_prompt_from_text(text, style)}
        )
        ad_meta = {"source": "none", "model": None, "style": style}
    else:
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
        seed = seed_from(text, title, author, direction.template, direction.ground)

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
            duotone_colours=darkest_and_lightest(hexes),
            style=style,
            quality=image_quality,
        )
        if art is None:
            notes.append(
                "image generation unavailable (no OPENAI_API_KEY or the call failed); "
                "used a procedural motif instead"
            )
            if direction.motif == "none":
                direction = direction.model_copy(
                    update={"motif": DRAWN_MOTIFS[seed % len(DRAWN_MOTIFS)]}
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

    # The brief is the single source of truth for copy; record who wrote each
    # piece so the book record can decide whether to adopt it.
    written_by = "fallback" if ad_meta.get("source") in ("fallback", "none") else "model"
    suggestions = {
        "genre_line": {
            "value": direction.genre_line,
            "source": "caller" if genre_line else written_by,
        },
    }

    content = Content(
        title=title,
        author=author,
        genre_line=direction.genre_line,
        imprint=imprint if imprint is not None else (
            fmt.imprint if fmt.imprint != "allgemein" else ""
        ),
    )

    ctx = Ctx(
        geo=geo,
        direction=direction,
        content=content,
        palette=direction.palette,
        seed=seed,
        artwork_uri=artwork_uri,
        marks=marks,
        notes=notes,
    )

    result = await asyncio.to_thread(render, ctx, outdir)

    return {
        "art_direction": direction.model_dump(),
        "art_direction_meta": ad_meta,
        "artwork": art_meta,
        "geometry": geo.to_dict(),
        "content": content.summary(),
        "suggestions": suggestions,
        "style": style,
        "director": director,
        "seed": seed,
        "image": str(result.image),
        "notes": result.notes,
    }
