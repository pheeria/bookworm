"""Art direction on OpenAI, as an alternative to the Claude director.

Produces the same :class:`~covers.artdirection.ArtDirection` brief from the same
style guidance, so the renderer cannot tell which director wrote it. Selected with
``director="openai"``.

Dropping to a single provider is a dependency and billing simplification, not a
latency one: the brief is a few seconds of a request the image call dominates.
``image_quality`` is the setting that moves the number (see the README).
"""

import logging
import os

from . import settings
from .artdirection import (
    DEFAULT_STYLE,
    ArtDirection,
    brief_schema,
    clip_prompt,
    degrade,
    system_prompt,
    user_prompt,
)
from .moods import Mood

log = logging.getLogger("covers.director_openai")

async def direct_openai(
    text: str,
    title: str,
    author: str,
    *,
    style: str = DEFAULT_STYLE,
    mood: Mood | None = None,
) -> tuple[ArtDirection, dict]:
    """Write the cover brief with an OpenAI text model.

    Falls back to the deterministic brief on any failure, matching the Claude
    director's contract: a missing key degrades the cover, not the request.
    """
    model = settings.openai_text_model()
    meta: dict = {"source": "openai", "model": model, "style": style}
    prompt_text, clipped = clip_prompt(text)
    meta["input_clipped"] = clipped

    if not os.environ.get("OPENAI_API_KEY"):
        log.info("OPENAI_API_KEY not set; using the deterministic brief")
        return degrade(meta, "no OPENAI_API_KEY", text, title, author, style, mood)

    import openai

    client = openai.AsyncOpenAI(timeout=120.0)
    user = user_prompt(title, author, prompt_text)

    try:
        response = await client.responses.parse(
            model=model,
            instructions=system_prompt(style, mood),
            input=user,
            text_format=brief_schema(mood),
        )
        direction = response.output_parsed
        if direction is None:
            raise ValueError("model returned no parsed output")
        usage = getattr(response, "usage", None)
        if usage is not None:
            meta["usage"] = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            }
        return direction, meta
    except (openai.OpenAIError, ValueError, TypeError) as exc:
        log.warning("openai art direction failed (%s), using fallback", exc)
        return degrade(meta, str(exc), text, title, author, style, mood)
