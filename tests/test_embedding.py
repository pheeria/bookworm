"""The `covers` package must be inert to import.

It is a library inside a larger application, so importing it must not load native
or provider dependencies it does not need, must not touch the host's environment,
and must not configure the host's logging. Each of those has already been true and
then quietly stopped being true once; this test is what makes it stay true.

Run in a child process because all three properties are about import time, and the
parent has already imported everything.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")

PROVIDERS = ("anthropic", "openai", "fastapi")
NATIVE = ("cairosvg", "cairocffi")

CHILD = textwrap.dedent(
    """
    import json, logging, os, sys

    def poison(names):
        for n in names:
            sys.modules[n] = None          # makes `import n` raise

    def loaded(names):
        return sorted(
            m for m in sys.modules
            if m.split(".")[0] in names and sys.modules[m] is not None
        )

    # Stage 1 -- the schema and the renderer must import with nothing available.
    # A host validating a request should not need libcairo installed.
    poison(("cairosvg", "cairocffi", "anthropic", "openai", "fastapi"))
    import covers
    import covers.models
    import covers.layout
    stage1 = loaded(("cairosvg", "cairocffi", "anthropic", "openai", "fastapi"))

    # Stage 2 -- the pipeline rasterises, so cairo is legitimately its business.
    # The provider SDKs are not: they belong behind function-level imports so a
    # cover can be rendered from a stored recipe without them.
    for n in ("cairosvg", "cairocffi"):
        del sys.modules[n]
    import covers.pipeline
    stage2 = loaded(("anthropic", "openai", "fastapi"))

    print(json.dumps({
        "env_leaked": os.environ.get("COVERS_CANARY"),
        "root_handlers": len(logging.getLogger().handlers),
        "stage1": stage1,
        "stage2": stage2,
    }))
    """
)


def test_importing_covers_is_inert(tmp_path):
    # A .env in the working directory is the host's business, not the library's.
    (tmp_path / ".env").write_text("COVERS_CANARY=leaked\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", CHILD],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr

    import json

    report = json.loads(result.stdout)

    assert report["stage1"] == [], (
        f"importing covers.models/covers.layout pulled in {report['stage1']}; the "
        "schema and the renderer must not need libcairo or a provider SDK"
    )
    assert report["stage2"] == [], (
        f"importing covers.pipeline pulled in {report['stage2']}; provider SDKs "
        "belong behind function-level imports"
    )
    assert report["env_leaked"] is None, (
        "importing covers loaded a .env into the process environment; one "
        "application loads one .env, and the library is not it"
    )
    assert report["root_handlers"] == 0, (
        "importing covers configured the root logger; that belongs to the app"
    )
