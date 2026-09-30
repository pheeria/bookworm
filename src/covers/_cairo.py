"""Make libcairo resolvable before cairocffi looks for it.

cairocffi loads libcairo by bare name through ``dlopen``, which on macOS does not
search Homebrew's prefix, so ``import cairosvg`` fails with "no library called
cairo-2 was found" even when cairo is installed. Extending
``DYLD_FALLBACK_LIBRARY_PATH`` in-process still works because the macOS ctypes
resolver re-reads the variable on every lookup.

Import this module before ``cairosvg`` anywhere it is needed; it re-exports
``svg2png`` so callers can just use this module instead.
"""

import os
import sys

_SEARCH_DIRS = (
    "/opt/homebrew/lib",  # Homebrew, Apple silicon
    "/usr/local/lib",  # Homebrew, Intel
    "/usr/lib/x86_64-linux-gnu",  # Debian/Ubuntu
    "/usr/lib/aarch64-linux-gnu",
)


def _extend_library_path() -> None:
    if sys.platform == "darwin":
        var = "DYLD_FALLBACK_LIBRARY_PATH"
    elif sys.platform.startswith("linux"):
        var = "LD_LIBRARY_PATH"
    else:
        return
    existing = [p for p in os.environ.get(var, "").split(os.pathsep) if p]
    additions = [d for d in _SEARCH_DIRS if os.path.isdir(d) and d not in existing]
    if additions:
        os.environ[var] = os.pathsep.join(additions + existing)


_extend_library_path()

try:
    from cairosvg import svg2png
except OSError as exc:  # pragma: no cover - environment-dependent
    raise OSError(
        "libcairo could not be loaded, so covers cannot be rasterised.\n"
        "  macOS:  brew install cairo\n"
        "  Debian: apt-get install libcairo2\n"
        f"Underlying error: {exc}"
    ) from exc

__all__ = ["svg2png"]
