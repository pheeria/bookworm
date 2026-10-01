"""The one thread all image work runs on: decoding, resampling, encoding, rasterising.

A thread that allocates a large image keeps that memory in its own pool once the
image is freed (glibc's arenas; macOS's zones), so image work spread over a pool
of threads holds a render's peak once per thread -- several hundred megabytes
after a few covers. On one thread the peak is held once. The work is CPU-bound,
so more threads would not make it faster on a small instance either; covers still
wait on their image models concurrently, only the pixel work queues.

On Linux, freed memory is also handed back to the system after each job.
"""

import asyncio
import ctypes
import ctypes.util
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import cache

_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="covers-image")


@cache
def _malloc_trim() -> Callable[[int], int] | None:
    """glibc's ``malloc_trim``, where there is one."""
    if not sys.platform.startswith("linux") or not (name := ctypes.util.find_library("c")):
        return None
    try:
        return ctypes.CDLL(name).malloc_trim
    except (OSError, AttributeError):  # not glibc (musl, say)
        return None


def _trimmed[T](fn: Callable[..., T], args: tuple) -> T:
    try:
        return fn(*args)
    finally:
        if trim := _malloc_trim():
            trim(0)


async def run[T](fn: Callable[..., T], *args) -> T:
    """``fn(*args)`` on the image thread."""
    return await asyncio.get_running_loop().run_in_executor(_THREAD, _trimmed, fn, args)
