"""The Buchkern and concept path, with Claude stubbed.

The stub stands in for ``anthropic.AsyncAnthropic``: ``messages.create`` is the
research call (web search), ``messages.parse`` answers with a Buchkern or with
concepts depending on the schema it is asked for. Every call is recorded, so the
tests can check what was sent and that nothing is asked twice.
"""

import json
from types import SimpleNamespace

import pytest

from books.db import SEED
from covers import concepts as covers_concepts
from covers.core import BookCore
from covers.moods import MOODS

SEEDED = json.loads(SEED.read_text(encoding="utf-8"))
BOOK = SEEDED[0]  # Alleinruhelage, Kiepenheuer & Witsch
SLUG = BOOK["slug"]

CORE = {
    "place_and_time": "ein Wochenendhaus im Wiener Umland, Gegenwart",
    "story": "Nach der Trennung renoviert eine Frau allein das Ferienhaus der Familie.",
    "emotional_core": "Verlust, Trotz und ein vorsichtiger Neuanfang",
    "motifs": ["ein halb gestrichenes Haus", "zwei Aschenbecher auf einer Veranda", "ein verwilderter Garten"],
    "twists": ["Idylle als Baustelle", "Hilfe von Fremden statt Familie"],
    "tone": ["lakonisch", "warm", "bitter"],
    "guardrails": "keine",
    "taboos": "keine",
    "recognition": "keine",
    "genre_hint": "Gegenwartsroman",
    "suspense_register": None,
    "rights": "frei",
    "typography": {
        "author": "Eva Menasse", "title": "Alleinruhelage", "subtitle": None,
        "genre": "Roman", "publisher_line": "Kiepenheuer & Witsch", "derived": [],
    },
    "fit": {"heart": "gut", "suspense": "ungeeignet", "trend": "möglich", "discourse": "gut"},
}


def _concept(motif: str, zone: str, family: str, ground: str) -> dict:
    return {
        "motif": motif, "twist": "a paint roller left mid-stroke", "composition": "low angle",
        "colour": "ochre, teal, off-white", "mode": None, "template": "picture",
        "type_zone": zone, "type_family": family, "title_case": "title", "ground": ground, "ink": "#1a1a18",
        "accent": "#c2703f", "secondary": "#89a8a0", "why": "Haus · Rolle · Ocker · Herz",
    }


CONCEPTS = {
    "concepts": [
        _concept("a half-painted wooden house", "top", "humanist", "#e8c86a"),
        _concept("two ashtrays on a veranda rail", "bottom", "garalde", "#cfe0b4"),
        _concept("an overgrown garden gate", "top", "literary_serif", "#f2c9c4"),
    ],
    "respect": "none",
    "avoid": "none",
}


def concepts_for(mood) -> dict:
    """CONCEPTS for heart; for another mood, the same concepts in its own layouts and faces."""
    if mood.key == "heart":
        return CONCEPTS
    return {**CONCEPTS, "concepts": [
        {**c, "template": mood.templates[i % len(mood.templates)],
         "type_family": mood.type_families[i % len(mood.type_families)]}
        for i, c in enumerate(CONCEPTS["concepts"])
    ]}


class _Claude:
    def __init__(self, calls: list, pause_first: bool = False):
        self.messages = self
        self.calls, self.pause_first = calls, pause_first

    async def create(self, **kw):
        self.calls.append(("research", kw))
        if self.pause_first and len([c for c in self.calls if c[0] == "research"]) == 1:
            return SimpleNamespace(stop_reason="pause_turn", content=[])
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="web_search_tool_result", content=[
                SimpleNamespace(url="https://de.wikipedia.org/wiki/Alleinruhelage"),
            ]),
            SimpleNamespace(type="text", text="- Stoff: eine Frau, ein Haus, zwei polnische Helfer"),
        ])

    async def parse(self, **kw):
        schema = kw["output_format"]
        if schema is BookCore:
            self.calls.append(("core", kw))
            return SimpleNamespace(stop_reason="end_turn", parsed_output=BookCore.model_validate(CORE))
        self.calls.append(("concepts", kw))
        mood = MOODS[schema.__name__.removeprefix("Concepts_")]
        return SimpleNamespace(stop_reason="end_turn", parsed_output=schema.model_validate(concepts_for(mood)))


@pytest.fixture
def claude(monkeypatch):
    """Claude as director, stubbed. Returns the list of recorded calls."""
    calls: list = []
    monkeypatch.setenv("COVERS_DIRECTOR", "claude")
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **_kw: _Claude(calls))
    return calls


def kinds(calls):
    return [kind for kind, _ in calls]


def test_a_cover_rests_on_the_researched_core_and_a_concept(client, claude):
    cover = client.post(f"/books/{SLUG}/covers", json={"type": "heart"}).json()
    assert kinds(claude) == ["research", "core", "concepts"]

    research = claude[0][1]
    [tool] = research["tools"]
    assert tool["type"] == "web_search_20260209"
    assert tool["allowed_domains"] == ["wikipedia.org", "goodreads.com"]
    assert "Alleinruhelage" in research["messages"][0]["content"]

    # The concept, not the fallback, made the cover.
    assert cover["art_direction_meta"]["source"] == "concept"
    assert cover["art_direction"]["template"] == "picture"
    assert cover["art_direction"]["type_zone"] == "top"
    assert cover["art_direction"]["type_family"] == "humanist"
    assert cover["color"] == "#e8c86a"
    assert cover["concept"] == 0 and len(cover["concepts"]["concepts"]) == 3
    # The genre line is the Buchkern's Typo-Daten Gattung.
    assert cover["content"]["genre_line"] == "Roman"

    prompt = cover["art_direction"]["image_prompt"]
    assert "Motif: a half-painted wooden house" in prompt
    assert covers_concepts.zone_text("picture", "top") in prompt
    assert "upper third" in prompt and "nothing behind them" in prompt
    assert "no text of any kind" in prompt and '"Alleinruhelage"' not in prompt


def test_the_core_is_researched_once_per_book(client, claude):
    for type in ("heart", "discourse"):
        client.post(f"/books/{SLUG}/covers", json={"type": type})
    assert kinds(claude) == ["research", "core", "concepts", "concepts"]
    core = client.get(f"/books/{SLUG}/core").json()
    assert core["sources"] == ["https://de.wikipedia.org/wiki/Alleinruhelage"]
    assert core["core"]["typography"]["author"] == "Eva Menasse"


def test_editing_the_book_rebuilds_its_core(client, claude):
    client.post(f"/books/{SLUG}/covers", json={"type": "heart"})
    client.put(f"/books/{SLUG}", json={**BOOK, "blurb": "Ein ganz anderer Klappentext."})
    client.post(f"/books/{SLUG}/covers", json={"type": "heart"})
    assert kinds(claude).count("research") == 2


def test_an_alternative_renders_without_new_concepts(client, claude):
    cover = client.post(f"/books/{SLUG}/covers", json={"type": "heart"}).json()
    again = client.post(
        f"/books/{SLUG}/covers/{cover['id']}/regenerate", json={"concept": 1}
    ).json()
    assert kinds(claude).count("concepts") == 1  # the stored ones were used
    assert again["concept"] == 1
    assert again["art_direction"]["type_zone"] == "bottom"
    assert again["art_direction"]["type_family"] == "garalde"
    assert again["color"] == "#cfe0b4"
    # A stored concept belongs to its type.
    other = client.post(
        f"/books/{SLUG}/covers/{cover['id']}/regenerate", json={"type": "trend", "concept": 1}
    )
    assert other.status_code == 422


def test_an_unsuitable_type_is_noted(client, claude):
    cover = client.post(f"/books/{SLUG}/covers", json={"type": "suspense"}).json()
    assert any("ungeeignet" in n for n in cover["notes"])


def test_a_paused_search_is_resumed(client, monkeypatch):
    calls: list = []
    monkeypatch.setenv("COVERS_DIRECTOR", "claude")
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **_kw: _Claude(calls, pause_first=True))
    client.post(f"/books/{SLUG}/covers", json={"type": "heart"})
    _, resumed = (kw for kind, kw in calls if kind == "research")
    assert resumed["messages"][-1]["role"] == "assistant"  # the paused turn, sent back


def test_without_claude_the_plain_brief_still_makes_the_cover(client, monkeypatch):
    monkeypatch.setenv("COVERS_DIRECTOR", "claude")  # but no credentials: the SDK fails
    cover = client.post(f"/books/{SLUG}/covers", json={"type": "heart"}).json()
    assert cover["art_direction_meta"]["source"] == "fallback"
    assert cover["concepts"] is None
    assert client.get(f"/books/{SLUG}/core").status_code == 404


def test_concepts_are_held_to_the_mood():
    import pydantic

    schema = covers_concepts.concepts_schema(MOODS["heart"])
    bad = {**CONCEPTS, "concepts": [{**CONCEPTS["concepts"][0], "type_family": "slab"}]}
    with pytest.raises(pydantic.ValidationError):
        schema.model_validate(bad)
