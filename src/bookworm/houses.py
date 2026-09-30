"""Publishers' formalities: the trim format and the imprint wordmark.

Formats come from ``covers.formats``, which already records each format's house
and binding; this module adds only what covers does not know -- the bindings a
house prints under an imprint of its own. It decides nothing about how the cover
feels: that follows the reader type (see ``covers.moods``).
"""

from books.models import Book
from covers.formats import DEFAULT_FORMAT, FORMATS

#: (publisher, binding) -> wordmark, where a binding appears under its own imprint.
IMPRINTS = {
    ("rowohlt", "taschenbuch"): "rororo",
    ("s. fischer", "taschenbuch"): "Fischer Taschenbuch",
}

#: Houses without a format of their own use the general one for the binding.
_GENERAL = "allgemein"
#: (house, binding) -> format key. Built in reverse so the first format listed
#: wins where a house has two for one binding (the general hardcovers).
_FORMATS = {(f.imprint, f.binding): f.key for f in reversed(FORMATS.values())}


def format_for(publisher: str, binding: str) -> str:
    """The house's own format for the binding, else a general one, else the default."""
    binding = binding.lower()
    return (
        _FORMATS.get((publisher, binding))
        or _FORMATS.get((_GENERAL, binding))
        or DEFAULT_FORMAT
    )


def formalities(book: Book) -> dict[str, str]:
    """The cover fields the publisher fixes, whoever the cover is for."""
    return {
        "format": format_for(book.publisher, book.format),
        "imprint": IMPRINTS.get((book.publisher.casefold(), book.format.casefold()), book.publisher),
    }
