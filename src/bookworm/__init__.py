"""bookworm -- print-ready book covers in German trade formats.

``.env`` is loaded here, before any submodule body runs, so that the
``BOOKWORM_*`` overrides those modules read at import time actually see it.
"""

from dotenv import load_dotenv

load_dotenv()
