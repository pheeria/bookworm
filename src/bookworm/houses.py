"""Publishers' formalities: what a cover of theirs must print, and at what size.

The house decides the trim format for each binding, the imprint wordmark, and
takes the Gattungsbezeichnung from the book. It decides nothing about how the
cover feels -- that follows the reader type (see ``covers.moods``). A publisher
not listed here gets generic formats and its own name as the wordmark.
"""

from dataclasses import dataclass, field

from books.models import Book
from covers.formats import DEFAULT_FORMAT

#: Formats for a binding when the house has none of its own in covers.
_GENERIC_FORMATS = {"Hardcover": "din_a5_hardcover"}


@dataclass(frozen=True)
class House:
    #: Book format (binding) -> covers format key.
    formats: dict[str, str] = field(default_factory=dict)
    #: Book format -> wordmark, where a binding appears under its own imprint.
    imprints: dict[str, str] = field(default_factory=dict)


HOUSES: dict[str, House] = {
    "Kiepenheuer & Witsch": House(
        formats={
            "Hardcover": "kiwi_hardcover",
            "Paperback": "kiwi_paperback",
            "Taschenbuch": "kiwi_taschenbuch",
            "Klappenbroschur": "kiwi_klappenbroschur",
        },
    ),
    "Rowohlt": House(
        formats={
            "Hardcover": "rowohlt_hardcover",
            "Paperback": "rowohlt_paperback",
            "Taschenbuch": "rororo_taschenbuch",
        },
        imprints={"Taschenbuch": "rororo"},
    ),
    "S. FISCHER": House(imprints={"Taschenbuch": "Fischer Taschenbuch"}),
    "Droemer Knaur": House(),
}


def formalities(book: Book) -> dict[str, str]:
    """The cover fields the publisher and the book fix, whoever the cover is for."""
    house = HOUSES.get(book.publisher, House())
    return {
        "format": house.formats.get(book.format)
        or _GENERIC_FORMATS.get(book.format, DEFAULT_FORMAT),
        "imprint": house.imprints.get(book.format, book.publisher),
        "genre_line": book.category,
    }
