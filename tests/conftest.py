"""Keep the test suite hermetic, fast and free.

``covers.main`` calls ``load_dotenv()`` at import, so a ``.env`` in the project
root puts real credentials into the environment. Without this fixture the suite
would make live Claude and image-generation calls: slow, non-deterministic, and
billed to whoever runs it.

Tests that exercise a provider path stub the client and set their own key, which
overrides this because they request their fixture after the autouse one.
"""

import pytest

_PROVIDER_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def no_live_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
