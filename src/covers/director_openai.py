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
    clip_prompt,
    degrade,
    system_prompt,
    user_prompt,
)

log = logging.getLogger("covers.director_openai")

async def direct_openai(
    text: str,
    title: str,
    author: str,
    *,
    style: str = DEFAULT_STYLE,
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
        return degrade(meta, "no OPENAI_API_KEY", text, title, author, style)

    import openai

    client = openai.AsyncOpenAI(timeout=120.0)
    user = user_prompt(title, author, prompt_text)

    try:
        response = await client.responses.parse(
            model=model,
            instructions=system_prompt(style),
            input=user,
            text_format=ArtDirection,
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
        return degrade(meta, str(exc), text, title, author, style)


#: Prefix that carries the register into a locally-composed image prompt, used by
#: the ``director="none"`` path where no text model runs at all.
_DIRECT_PREAMBLE = {
    "illustrated": (
        "A warm figurative illustration in gouache and coloured pencil, visible "
        "hand and texture, generous hand-mixed colour, for the cover of a German "
        "literary novel. Depict the central place or object of this story:"
    ),
    "painterly": (
        "An oil painting with real brushwork, atmospheric and tonal, for the cover "
        "of a German literary novel. Depict the central place or scene of this "
        "story:"
    ),
    "typographic": (
        "An abstract composition of a few flat overlapping planes with matte "
        "gouache texture, for the cover of a German literary novel, suggesting:"
    ),
}

#: How much of the book text is handed to the image model on the direct path.
DIRECT_TEXT_CHARS = 700


def direct_prompt_from_text(text: str, style: str = DEFAULT_STYLE) -> str:
    """Compose an image prompt locally, with no text-model call.

    This is the genuinely single-call path: the book's own words are handed to the
    image model behind a register preamble. It costs no extra latency, and it gives
    up everything the brief decides -- palette, layout and German genre line all
    come from the deterministic fallback instead.
    """
    preamble = _DIRECT_PREAMBLE.get(style, _DIRECT_PREAMBLE[DEFAULT_STYLE])
    body = " ".join(text.split())[:DIRECT_TEXT_CHARS]
    return f"{preamble} {body}"
