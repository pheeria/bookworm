"""Orchestration: prompt in, print-ready front cover out.

    text/title/author
        -> Claude writes the art direction and the image prompt
        -> an image model (fal or OpenAI) paints the artwork (optional)
        -> the typography engine sets the type in German trade geometry
        -> front.png
"""

import logging

from . import imagegen, imagethread, lettering, settings
from .artdirection import (
    DEFAULT_STYLE,
    DRAWN_MOTIFS,
    ArtDirection,
    apply_overrides,
    direct,
    fallback_direction,
    seed_from,
)
from .director_openai import direct_openai
from .formats import geometry, px, resolve_format
from .layout import Content, Ctx, artwork_plan
from .moods import MOODS
from .palettes import darkest_and_lightest
from .render import render
from .typography import TYPE_FAMILIES

log = logging.getLogger("covers.pipeline")


async def _adjust_lettering(
    direction: ArtDirection, image, *, title: str, author: str, families: tuple[str, ...]
) -> tuple[ArtDirection, dict]:
    """The brief with its lettering confirmed or moved by a look at the painted picture."""
    placed = await lettering.adjust(image, direction, title=title, author=author, families=families)
    if placed is None:
        return direction, {"source": "plan"}
    # Validated whole, so the blackletter casing rule holds for the new face too.
    adjusted = ArtDirection.model_validate(direction.model_dump() | placed.model_dump(exclude={"note"}))
    return adjusted, {"source": "vision", "note": placed.note}


def _with_motif(direction: ArtDirection, seed: int, *, drawn: bool = False) -> ArtDirection:
    """A cover drawing a procedural motif -- asked for, or standing in for a picture
    that could not be painted -- always has one, even if the brief chose none."""
    if direction.motif != "none" or not (drawn or direction.artwork == "procedural"):
        return direction
    return direction.model_copy(update={"motif": DRAWN_MOTIFS[seed % len(DRAWN_MOTIFS)]})


async def create_cover(
    *,
    text: str,
    title: str,
    author: str,
    format: str | None = None,
    dpi: int = 300,
    imprint: str | None = None,
    template: str | None = None,
    type_family: str | None = None,
    palette: str | None = None,
    artwork: str | None = None,
    motif: str | None = None,
    genre_line: str | None = None,
    mood: str | None = None,
    style: str | None = None,
    director: str | None = None,
    treatment: str = "none",
    image_quality: str | None = None,
    image_model: imagegen.ImageModel = imagegen.DEFAULT_IMAGE_MODEL,
    seed: int | None = None,
    marks: bool = False,
    brief: ArtDirection | None = None,
    brief_meta: dict | None = None,
) -> dict:
    """Render a front cover. ``brief`` skips the art director: the caller wrote it."""
    fmt = resolve_format(format)
    geo = geometry(fmt, dpi=dpi)

    profile = MOODS[mood] if mood else None
    style = style or (profile.style if profile else DEFAULT_STYLE)
    director = director or settings.director()

    if brief is not None:
        direction, ad_meta = brief, {"style": style, **(brief_meta or {"source": "brief"})}
    elif director == "openai":
        direction, ad_meta = await direct_openai(text, title, author, style=style, mood=profile)
    elif director == "none":
        direction = fallback_direction(text, title, author, style, profile)
        ad_meta = {"source": "none", "model": None, "style": style}
    else:
        direction, ad_meta = await direct(text, title, author, style=style, mood=profile)
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
    direction = _with_motif(direction, seed)

    notes: list[str] = []
    artwork_image = None
    art_meta: dict | None = None

    plan = artwork_plan(direction, geo)
    if direction.artwork == "generated" and plan is None:
        notes.append(
            f"template {direction.template!r} is typographic; generated artwork skipped"
        )
    elif direction.artwork == "generated":
        if direction.template == "picture":
            # Whoever wrote the prompt, the picture leaves room for the type.
            direction = direction.model_copy(update={
                "image_prompt": f"{direction.image_prompt} {lettering.zone_text(direction.lettering)}."
            })
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
            model=image_model,
        )
        if isinstance(art, imagegen.Unavailable):
            notes.append(
                f"image generation with {image_model} unavailable ({art.reason}); used a "
                "procedural motif instead"
            )
            direction = _with_motif(direction, seed, drawn=True)
        else:
            artwork_image = art.image
            art_meta = dict(art.meta)
            art_meta["placement_mm"] = [round(v, 2) for v in plan]
            art_meta["effective_dpi"] = min(
                imagegen.effective_dpi(art.meta["native_px"][0], plan[2]),
                imagegen.effective_dpi(art.meta["native_px"][1], plan[3]),
            )
            if art_meta["effective_dpi"] < dpi:
                notes.append(
                    f"artwork is {art_meta['effective_dpi']} dpi native over its "
                    f"placement and was resampled up to {dpi} dpi; type stays vector"
                )
            # Claude looks at the picture before the type goes on; without
            # credentials the look is skipped and the plan stands.
            if direction.template == "picture" and director == "claude":
                families = (type_family,) if type_family else TYPE_FAMILIES
                direction, ad_meta["lettering"] = await _adjust_lettering(
                    direction, art.image, title=title, author=author, families=families,
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
        seed=seed,
        artwork_image=artwork_image,
        marks=marks,
    )

    png = await imagethread.run(render, ctx)

    return {
        "art_direction": direction.model_dump(),
        "art_direction_meta": ad_meta,
        "artwork": art_meta,
        "geometry": geo.to_dict(),
        "content": content.summary(),
        "suggestions": suggestions,
        "mood": mood,
        "style": style,
        "director": director,
        "seed": seed,
        "png": png,
        "notes": notes,
    }
