"""Keep the test suite hermetic, fast and free.

``bookworm.main`` calls ``load_dotenv()`` at import, so a ``.env`` in the project
root puts real credentials into the environment. Without this fixture the suite
would make live Claude and image-generation calls: slow, non-deterministic, and
billed to whoever runs it.

Tests that exercise a provider path stub the client and set their own key, which
overrides this because they request their fixture after the autouse one.
"""

import mongomock
import mongomock.gridfs
import pytest
from fastapi.testclient import TestClient

# Cover images go to GridFS; mongomock only supports it once this has run.
mongomock.gridfs.enable_gridfs_integration()

_PROVIDER_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "OPENAI_API_KEY",
    "MONGODB_URI",
    "MONGODB_USERNAME",
    "MONGODB_PASSWORD",
)


@pytest.fixture(autouse=True)
def no_live_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client() -> TestClient:
    """The bookworm app on a fresh in-memory database; the lifespan seeds it."""
    from bookworm.main import create_app

    with TestClient(create_app(mongomock.MongoClient)) as c:
        yield c


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Where the standalone /generate writes its PNGs, for this test only."""
    monkeypatch.setenv("COVERS_OUTPUT_DIR", str(tmp_path))
    return tmp_path
