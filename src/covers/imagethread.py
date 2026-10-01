"""Image memory: one thread for the pixel work, and an allocator that gives it back.

A render briefly holds a hundred megabytes or so of pixels. ``run`` puts all image
work -- decoding, resampling, rasterising -- on one thread, so however many covers
are generated at once, only one such peak exists at a time. The work is CPU-bound,
so more threads would not make it faster on a small instance; covers still wait on
their image models concurrently, only the pixel work queues.

``tune_allocator`` is for the process that serves covers to call at startup. glibc
otherwise keeps freed memory in per-thread arenas and, once large blocks have been
freed, raises the size above which it maps blocks rather than pooling them --
either way a render's peak stays resident after the render is done.
"""

import asyncio
import ctypes
import ctypes.util
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="covers-image")

#: ``mallopt`` parameters (glibc's malloc.h).
_M_MMAP_THRESHOLD, _M_ARENA_MAX = -3, -8


async def run[T](fn: Callable[..., T], *args) -> T:
    """``fn(*args)`` on the image thread."""
    return await asyncio.get_running_loop().run_in_executor(_THREAD, fn, *args)


def tune_allocator() -> None:
    """Have glibc map every block of a megabyte or more, so it goes back to the
    system when freed, and keep two arenas, not one per thread. Elsewhere, nothing."""
    if not sys.platform.startswith("linux") or not (name := ctypes.util.find_library("c")):
        return
    try:
        mallopt = ctypes.CDLL(name).mallopt
    except (OSError, AttributeError):  # not glibc (musl, say)
        return
    mallopt(_M_MMAP_THRESHOLD, 1 << 20)
    mallopt(_M_ARENA_MAX, 2)
