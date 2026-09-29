#!/usr/bin/env python
"""Generate a cover from an EPUB's own metadata and opening text.

    uv run scripts/from_epub.py examples/die-verwandlung.epub
    uv run scripts/from_epub.py examples/*.epub --format rororo_taschenbuch

Reads the title, author and page estimate straight out of the container, so the
sample books in examples/ can be used as real input without retyping anything.
Talks to the running API by default; --local skips HTTP and calls the pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

DC = "{http://purl.org/dc/elements/1.1/}"
CONTAINER = "META-INF/container.xml"


def _opf_path(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read(CONTAINER))
    rootfile = root.find(".//{*}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("no rootfile in META-INF/container.xml")
    return rootfile.get("full-path")  # type: ignore[return-value]


def _strip_markup(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", markup)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", markup))
    return re.sub(r"\s+", " ", text).strip()


def read_epub(path: Path, excerpt_chars: int = 3000) -> dict:
    """Title, author, an excerpt, and a page estimate from character count."""
    with zipfile.ZipFile(path) as zf:
        opf_name = _opf_path(zf)
        opf = ET.fromstring(zf.read(opf_name))
        base = str(Path(opf_name).parent)

        def meta(tag: str) -> str:
            el = opf.find(f".//{DC}{tag}")
            return (el.text or "").strip() if el is not None else ""

        title = meta("title") or path.stem.replace("-", " ").title()
        author = meta("creator") or "Unbekannt"
        description = meta("description")

        manifest = {
            item.get("id"): item.get("href")
            for item in opf.findall(".//{*}manifest/{*}item")
            if (item.get("media-type") or "").startswith("application/xhtml")
        }
        spine = [
            manifest.get(ref.get("idref"))
            for ref in opf.findall(".//{*}spine/{*}itemref")
        ]

        chunks: list[str] = []
        total_chars = 0
        for href in [h for h in spine if h]:
            name = str(Path(base) / href) if base not in (".", "") else href
            try:
                body = _strip_markup(zf.read(name).decode("utf-8", "replace"))
            except KeyError:
                continue
            total_chars += len(body)
            if sum(len(c) for c in chunks) < excerpt_chars and len(body) > 200:
                chunks.append(body)

    excerpt = " ".join(chunks)[:excerpt_chars]
    # ~1800 characters to a typeset page is a standard German trade estimate.
    pages = max(48, round(total_chars / 1800 / 2) * 2)
    return {
        "title": title,
        "author": author,
        "text": (description + "\n\n" + excerpt).strip() if description else excerpt,
        "pages": pages,
        "chars": total_chars,
    }


async def run_local(book: dict, outdir: Path, **overrides) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from bookworm.pipeline import create_cover

    return await create_cover(
        text=book["text"],
        title=book["title"],
        author=book["author"],
        pages=book["pages"],
        outdir=outdir,
        assets=("front_png", "front_svg", "spread_pdf", "direction_json"),
        **overrides,
    )


def run_http(book: dict, base_url: str, **overrides) -> dict:
    import httpx

    payload = {
        "text": book["text"],
        "title": book["title"],
        "author": book["author"],
        "pages": book["pages"],
        **overrides,
    }
    response = httpx.post(f"{base_url}/generate", json=payload, timeout=300)
    response.raise_for_status()
    return response.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("epubs", nargs="+", type=Path)
    ap.add_argument("--format", dest="format_key", default=None)
    ap.add_argument("--template", default=None)
    ap.add_argument("--palette", default=None)
    ap.add_argument("--artwork", default=None, choices=["generated", "procedural", "none"])
    ap.add_argument("--local", action="store_true", help="Skip HTTP, call the pipeline.")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--outdir", type=Path, default=Path("out"))
    args = ap.parse_args()

    overrides = {
        k: v
        for k, v in (
            ("template", args.template),
            ("palette", args.palette),
            ("artwork", args.artwork),
        )
        if v is not None
    }

    for epub in args.epubs:
        book = read_epub(epub)
        print(f"\n{epub.name}")
        print(f"  {book['author']} — {book['title']}")
        print(f"  {book['chars']:,} characters → {book['pages']} pages")
        if args.local:
            result = asyncio.run(
                run_local(book, args.outdir / epub.stem, format_key=args.format_key, **overrides)
            )
            for name, path in result["files"].items():
                print(f"  {name}: {path}")
        else:
            result = run_http(
                book, args.base_url, format=args.format_key, **overrides
            )
            for name, url in result["assets"].items():
                print(f"  {name}: {args.base_url}{url}")
        for note in result.get("notes", []):
            print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
