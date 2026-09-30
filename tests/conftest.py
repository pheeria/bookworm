"""Keep the test suite hermetic, fast and free.

A ``.env`` in the project root holds real credentials; a test that reached them
would make live, billed Claude and image-generation calls. Three layers stop it:

- Tests build apps with ``create_app()``, which reads no ``.env``. Only the served
  ``bookworm.main:app`` loads it, and no test touches that attribute.
- This fixture clears any provider keys the shell itself exported, and makes the
  deterministic brief the default director.
- Even if a key does leak, both SDKs are pointed at a port nothing listens on,
  so a live call fails in milliseconds instead of succeeding.

Tests that exercise a provider path stub the client and set their own key, which
overrides this because they request their fixture after the autouse one.
"""

import mongomock
import mongomock.gridfs
import pytest
from fastapi.testclient import TestClient

from bookworm.main import create_app

# Cover images go to GridFS; mongomock only supports it once this has run.
mongomock.gridfs.enable_gridfs_integration()

_PROVIDER_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "OPENAI_API_KEY",
    "FAL_API_KEY",
    "FAL_KEY",
    "MONGODB_URI",
    "MONGODB_USERNAME",
    "MONGODB_PASSWORD",
)


#: Discard port on loopback: connections are refused at once.
_NOWHERE = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def no_live_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", _NOWHERE)
    monkeypatch.setenv("OPENAI_BASE_URL", _NOWHERE)
    monkeypatch.setenv("COVERS_DIRECTOR", "none")


@pytest.fixture
def client() -> TestClient:
    """The bookworm app on a fresh in-memory database; the lifespan seeds it."""
    with TestClient(create_app(mongomock.MongoClient)) as c:
        yield c


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Where the standalone /generate writes its PNGs, for this test only."""
    monkeypatch.setenv("COVERS_OUTPUT_DIR", str(tmp_path))
    return tmp_path
