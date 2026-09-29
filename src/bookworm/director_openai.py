"""Art direction on OpenAI, as an alternative to the Claude director.

Produces the same :class:`~bookworm.artdirection.ArtDirection` brief from the same
style guidance, so the renderer cannot tell which director wrote it. Selected with
``director="openai"``.

Worth knowing what this does and does not buy you. Measured on one cover
(``kiwi_klappenbroschur``, 304 pp, ``illustrated``): the brief is ~9 s of a ~144 s
request and the image generation is the other ~135 s. Dropping to a single provider
is a dependency and billing simplification, not a latency one -- ``image_quality``
is the setting that moves the number.
"""

from __future__ import annotations

import logging
import os

from .artdirection import (
    DEFAULT_STYLE,
    ArtDirection,
    _clip,
    fallback_direction,
    system_prompt,
)

log = logging.getLogger("bookworm.director_openai")

MODEL = os.environ.get("BOOKWORM_OPENAI_TEXT_MODEL", "gpt-5.4")


async def direct_openai(
    text: str,
    title: str,
    author: str,
    *,
    style: str = DEFAULT_STYLE,
    model: str | None = None,
    timeout: float = 120.0,
) -> tuple[ArtDirection, dict]:
    """Write the cover brief with an OpenAI text model.

    Falls back to the deterministic brief on any failure, matching the Claude
    director's contract: a missing key degrades the cover, not the request.
    """
    model = model or MODEL
    meta: dict = {"source": "openai", "model": model, "style": style}
    prompt_text, clipped = _clip(text)
    meta["input_clipped"] = clipped

    if not os.environ.get("OPENAI_API_KEY"):
        log.info("OPENAI_API_KEY not set; using the deterministic brief")
        meta.update(source="fallback", reason="no OPENAI_API_KEY")
        return fallback_direction(text, title, author, style), meta

    try:
        import openai
        from openai import AsyncOpenAI
    except ImportError:  # pragma: no cover
        meta.update(source="fallback", reason="openai package not installed")
        return fallback_direction(text, title, author, style), meta

    client = AsyncOpenAI(timeout=timeout)
    user = (
        f"Titel: {title}\n"
        f"Autor/in: {author}\n\n"
        f"Vorgabe/Text:\n{prompt_text}\n\n"
        "Brief den Umschlag."
    )

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
        meta.update(source="fallback", reason=str(exc))
        return fallback_direction(text, title, author, style), meta


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
    up everything the brief decides -- palette, layout, German genre line and
    back-cover copy all come from the deterministic fallback instead.
    """
    preamble = _DIRECT_PREAMBLE.get(style, _DIRECT_PREAMBLE[DEFAULT_STYLE])
    body = " ".join(text.split())[:DIRECT_TEXT_CHARS]
    return f"{preamble} {body}"
