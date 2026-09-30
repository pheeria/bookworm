"""books -- the book catalogue, with CRUD over a SQLite store.

A library, not a service: like ``covers``, nothing here configures the process.
The composing application calls ``books.db.init`` and mounts ``books.router``.
"""

from .router import router

__all__ = ["router"]
