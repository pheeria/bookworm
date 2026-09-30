"""``COVERS_*`` settings, read when used rather than at import.

Reading them lazily means the order of ``.env`` loading and imports does not
matter, and a test can set the environment instead of patching module globals.
"""

import os
from pathlib import Path


def claude_model() -> str:
    return os.environ.get("COVERS_CLAUDE_MODEL", "claude-opus-5")


def openai_text_model() -> str:
    return os.environ.get("COVERS_OPENAI_TEXT_MODEL", "gpt-5.4")


def openai_image_model() -> str:
    """The OpenAI model used when a cover asks for ``image_model="openai"``.

    COVERS_IMAGE_MODEL is the name this setting had before other image models came.
    """
    return os.environ.get("COVERS_OPENAI_IMAGE_MODEL") or os.environ.get(
        "COVERS_IMAGE_MODEL", "gpt-image-2"
    )


def director() -> str:
    """Who writes the brief when a request does not say: claude, openai or none."""
    return os.environ.get("COVERS_DIRECTOR", "claude")


def image_quality() -> str:
    return os.environ.get("COVERS_IMAGE_QUALITY", "high")


def output_dir() -> Path:
    """Where the standalone ``/generate`` endpoint keeps its PNGs."""
    return Path(os.environ.get("COVERS_OUTPUT_DIR", "out")).resolve()


def log_level() -> str:
    return os.environ.get("COVERS_LOG_LEVEL", "INFO")


def system_fonts() -> bool:
    """Prefer the macOS faces where installed. Off shows what a deploy renders."""
    return os.environ.get("COVERS_SYSTEM_FONTS", "1").strip().lower() not in ("0", "false", "no")
