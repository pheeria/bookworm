"""The Buchkern: what every cover of one title is built on (Prompt A).

Two Claude calls, made once per title and cached by the caller:

1. ``research`` searches Wikipedia and Goodreads for the book and writes notes,
   so a cover can rest on the story rather than on the blurb alone.
2. ``write_core`` turns the title data plus those notes into the Buchkern: place
   and time, the story in a sentence, showable motifs, twists, tone, guardrails,
   taboos, rights, the exact typography data, and how well each reader type fits.

They are separate calls because structured output cannot be combined with the
citations web search produces. Both degrade to ``None`` -- no key, an API error,
a refusal -- and the caller falls back to the plain brief.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from . import settings

log = logging.getLogger("covers.core")

#: Where the research may look. Goodreads has no public API, so web search it is.
RESEARCH_DOMAINS = ["wikipedia.org", "goodreads.com"]
MAX_SEARCHES = 5
MAX_CONTINUATIONS = 3

FitLevel = Literal["gut", "möglich", "ungeeignet"]
Rights = Literal["frei", "Coverübernahme – Freigabe nötig", "Übersetzung – Lizenz prüfen"]


class Research(BaseModel):
    notes: str
    sources: list[str]


class TypoData(BaseModel):
    author: str = Field(description="Autor*in, voller Name, exakt mit Umlauten und Akzenten.")
    title: str = Field(description="Titel ohne Zusätze in Klammern.")
    subtitle: str | None = Field(description="Echter Untertitel oder Reihe, sonst null.")
    genre: str = Field(description="Gattung, z. B. „Roman“ oder „Kriminalroman“.")
    publisher_line: str = Field(description="Verlagszeile, genau wie vorgegeben.")
    derived: list[str] = Field(description="Welche dieser Angaben abgeleitet statt gegeben sind.")


class ReaderFit(BaseModel):
    """Einordnung: wie gut der Titel zu jedem Lesetyp passt."""

    heart: FitLevel = Field(description="HERZ")
    suspense: FitLevel = Field(description="SPANNUNG")
    trend: FitLevel = Field(description="TREND")
    discourse: FitLevel = Field(description="DISKURS")


class BookCore(BaseModel):
    place_and_time: str = Field(description="Ort und Zeit: ein Satz.")
    story: str = Field(description="Stoff in einem Satz.")
    emotional_core: str = Field(description="Emotionaler Kern: höchstens 10 Wörter.")
    motifs: list[str] = Field(description="Zentrale Motive: 3 bis 5 zeigbare Motive, je ein kurzer Ausdruck.")
    twists: list[str] = Field(
        description="Kniff-Ansätze: 2 bis 3 Spannungen oder Widersprüche aus dem Stoff, die sich als Bild-Irritation eignen."
    )
    tone: list[str] = Field(description="Tonalität: 3 Adjektive.")
    guardrails: str = Field(description="Leitplanken: verbindliche Vorgaben aus den Titeldaten, sonst „keine“.")
    taboos: str = Field(description="Tabus: was kein Cover zeigen darf, sonst „keine“.")
    recognition: str = Field(
        description="Wiedererkennung: prägendes Element einer Reihe oder eines Vorgängers, sonst „keine“."
    )
    genre_hint: str = Field(description="Genre-Indiz: das Hauptgenre.")
    suspense_register: Literal["düster", "dramatisch"] | None = Field(
        description="Bei SPANNUNG die Ausprägung: „düster“ (Krimi/Thriller/Horror) oder „dramatisch“; sonst null."
    )
    rights: Rights
    typography: TypoData
    fit: ReaderFit


_RESEARCH_SYSTEM = """\
Du recherchierst für die Umschlaggestaltung eines Buchs. Suche das Buch auf Wikipedia \
(deutsch oder englisch) und auf Goodreads, anhand von Titel und Autor*in.

Fasse zusammen, was die Quellen belegen: Stoff und Handlung, Schauplatz und Zeit, \
zentrale Figuren, Ton und Atmosphäre, wiederkehrende Bilder und Gegenstände, ob das Buch \
eine Übersetzung ist (Originaltitel und -sprache), ob es zu einer Reihe gehört, und \
gegebenenfalls, welche Themen heikel sind (Gewalt, Tod). Keine Bewertungen, keine \
Verkaufszahlen, keine Preise. Findest du das Buch nicht, sag das in einem Satz und \
erfinde nichts. Antworte in knappen deutschen Stichpunkten."""

_CORE_SYSTEM = """\
Du bist Art Director bei {publisher}. Du erhältst die Titeldaten eines Titels und eine \
Recherche aus Wikipedia und Goodreads. Erstelle daraus den BUCHKERN: die Grundlage für \
alle Cover-Varianten dieses Titels, unabhängig vom Lesetyp.

Maßstab: die aktuellen Umschläge des Hauses. Ein einziges, sofort lesbares Motiv, eine \
präzise Irritation („Kniff“), eine klare Farbentscheidung. Die Typografie wird danach \
exakt gesetzt, deshalb brauchst du die exakten Textdaten.

Regeln
1. Nutze nur Informationen aus den Titeldaten und der Recherche; widersprechen sie sich, \
gelten die Titeldaten. Motive müssen sichtbar sein: Gegenstände, Orte, Landschaften, Tiere, \
Figuren nur klein oder als Detail. Keine abstrakten Begriffe als Motiv.
2. Vorgaben oder Verbote zum Cover in den Titeldaten sind verbindlich; explizite Verbote \
übernimmst du wörtlich in die Leitplanken. Gibt es keine, leitest du die Motive aus dem \
Klappentext und der Recherche ab.
3. Keine Markennamen (aus „roter Lada“ wird „altes rotes Auto“), keine realen Personen, \
keine geschützten Kunstwerke.
4. Autorennamen und Vergleichstitel sind keine Motive.
5. Sensible Stoffe (Gewalt, sexualisierte Gewalt, Tod) gehören in die Tabus und werden nie \
gezeigt oder angedeutet.
6. Ignoriere Marketingangaben (Termine, Preise, Auszeichnungen, Bestsellerplätze, \
Verkaufszahlen, Ausstattung wie Farbschnitt) und alle Personendaten außer dem Namen.
7. Rechte: Nennen die Titeldaten einen fremden Coverentwurf („nach einem Entwurf von“), \
setze „Coverübernahme – Freigabe nötig“. Ist der Titel laut Recherche eine Übersetzung, \
setze „Übersetzung – Lizenz prüfen“. Sonst „frei“.
8. Typo-Daten exakt, mit Umlauten und Akzenten: Autor*in und Titel wie in den Titeldaten, \
ohne Zusätze wie „[1]“ oder „(KiWi 1905)“; ein Untertitel nur, wenn er ein echter \
Untertitel oder eine Reihe ist, nicht Werbung oder Gattung; die Gattung aus der Kategorie \
oder abgeleitet (Krimi → „Kriminalroman“, sonst „Roman“); die Verlagszeile genau \
„{imprint}“. Keine Zitate, Störer oder Claims. Abgeleitete Angaben nennst du in „derived“.
9. Einordnung: bewerte für jeden Lesetyp, wie gut der Titel zu ihm passt, mit „gut“, \
„möglich“ oder „ungeeignet“: HERZ (Gefühl, Liebe, Familie, Feel-good), SPANNUNG (Krimi, \
Thriller, das Psychologische), TREND (BookTok, Bestseller, Romantasy), DISKURS (Literatur, \
Essay, Sachbuch).

Schreibe alle Felder auf Deutsch."""


def _title_data(details: dict[str, str]) -> str:
    return "\n".join(f"{name}: {value}" for name, value in details.items() if value)


def _failed(what: str, exc: Exception, meta: dict | None) -> None:
    log.warning("%s failed (%s)", what, exc)
    if meta is not None:
        meta["failure"] = str(exc)


async def parse[T: BaseModel](
    schema: type[T], *, system: str, content: str | list, what: str,
    max_tokens: int = 16000, meta: dict | None = None,
) -> T | None:
    """One structured-output call to Claude; None without credentials, on an error
    or a refusal, so every caller can fall back. ``meta``, if given, gets the
    token usage, or why the call failed."""
    import anthropic

    try:
        claude = anthropic.AsyncAnthropic()
        response = await claude.messages.parse(
            model=settings.claude_model(),
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
    # Credentials that cannot be resolved surface only once a request is made.
    except (anthropic.AnthropicError, TypeError, ValueError) as exc:
        _failed(what, exc, meta)
        return None
    if response.stop_reason == "refusal" or response.parsed_output is None:
        _failed(what, ValueError(f"no answer ({response.stop_reason})"), meta)
        return None
    if meta is not None:
        meta["usage"] = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    return response.parsed_output


async def research(details: dict[str, str]) -> Research | None:
    """Notes on the book from Wikipedia and Goodreads, with the pages they came
    from. Empty notes when nothing was found; None when the research failed."""
    import anthropic

    user = {"role": "user", "content": _title_data(details)}
    tools = [{
        "type": "web_search_20260209",
        "name": "web_search",
        "allowed_domains": RESEARCH_DOMAINS,
        "max_uses": MAX_SEARCHES,
    }]
    # A long search pauses; sending back everything so far resumes it, and each
    # continuation returns only its new blocks.
    messages: list = [user]
    blocks: list = []
    try:
        claude = anthropic.AsyncAnthropic()
        for _ in range(MAX_CONTINUATIONS + 1):
            response = await claude.messages.create(
                model=settings.claude_model(),
                max_tokens=8000,
                system=_RESEARCH_SYSTEM,
                thinking={"type": "adaptive"},
                tools=tools,
                messages=messages,
            )
            blocks += response.content
            if response.stop_reason != "pause_turn":
                break
            messages = [user, {"role": "assistant", "content": blocks}]
    except (anthropic.AnthropicError, TypeError) as exc:
        _failed("book research", exc, None)
        return None
    if response.stop_reason in ("pause_turn", "refusal"):
        _failed("book research", ValueError(f"no answer ({response.stop_reason})"), None)
        return None

    notes = "\n".join(b.text for b in blocks if b.type == "text").strip()
    sources: list[str] = []
    for block in blocks:
        # A failed search returns an error object here, not a list of results.
        if block.type == "web_search_tool_result" and isinstance(block.content, list):
            sources += [r.url for r in block.content if r.url not in sources]
    return Research(notes=notes, sources=sources)


async def write_core(
    details: dict[str, str], found: Research | None, *, publisher: str, imprint: str
) -> BookCore | None:
    """The Buchkern for one title, from its data and the research notes."""
    content = f"Titeldaten:\n{_title_data(details)}"
    notes = found.notes if found else ""
    content += f"\n\nRecherche:\n{notes}" if notes else "\n\nRecherche: keine Ergebnisse."
    return await parse(
        BookCore, system=_CORE_SYSTEM.format(publisher=publisher, imprint=imprint),
        content=content, what="book core",
    )


def enabled() -> bool:
    """The concept path needs Claude, whose web search is Anthropic's. Without
    credentials the calls return None and the caller falls back to the plain brief."""
    return settings.director() == "claude"
