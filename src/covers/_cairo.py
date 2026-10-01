"""Make libcairo resolvable before cairocffi looks for it.

cairocffi loads libcairo by bare name through ``dlopen``, which on macOS does not
search Homebrew's prefix, so ``import cairosvg`` fails with "no library called
cairo-2 was found" even when cairo is installed. Extending
``DYLD_FALLBACK_LIBRARY_PATH`` in-process still works because the macOS ctypes
resolver re-reads the variable on every lookup.

Only macOS needs this. Import this module before ``cairosvg`` anywhere it is needed; its
``svg2png`` stands in for cairosvg's, so callers can just use this module.
"""

import os
import sys

#: Homebrew's library directories, Apple silicon and Intel.
_HOMEBREW = ("/opt/homebrew/lib", "/usr/local/lib")
#: What dyld searches when the variable is unset; setting it replaces these.
_DYLD_DEFAULT = (os.path.expanduser("~/lib"), "/usr/local/lib", "/usr/lib")


def _extend_library_path() -> None:
    if sys.platform != "darwin":
        return  # elsewhere the loader already searches the standard directories
    var = "DYLD_FALLBACK_LIBRARY_PATH"
    existing = [p for p in os.environ.get(var, "").split(os.pathsep) if p] or list(_DYLD_DEFAULT)
    additions = [d for d in _HOMEBREW if os.path.isdir(d) and d not in existing]
    if additions:
        os.environ[var] = os.pathsep.join(additions + existing)


_extend_library_path()

try:
    from cairosvg.surface import PNGSurface
except OSError as exc:  # pragma: no cover - environment-dependent
    raise OSError(
        "libcairo could not be loaded, so covers cannot be rasterised.\n"
        "  macOS:  brew install cairo\n"
        "  Debian: apt-get install libcairo2\n"
        f"Underlying error: {exc}"
    ) from exc


def svg2png(bytestring: bytes, **kwargs) -> bytes:
    """``cairosvg.svg2png``, but passing on ``url_fetcher``, which it does not."""
    return PNGSurface.convert(bytestring, **kwargs)


__all__ = ["svg2png"]
