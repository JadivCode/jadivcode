"""Standard libraries of JadivCode.

A library is a small Python class.  The interpreter talks to it through
three methods:

``query(args)``            used in expressions:  ``{Math "5x5"}``
``statement(args, value)`` used as a statement:  ``GPIO Write #4 = ON``
``subscribe(args, cb)``    used by ``On``:       ``On GUI Read Button ...``

Objects returned from ``query`` (pins, windows, pages, servers) may offer
``jc_get``, ``jc_set``, ``jc_set_prop``, ``jc_start``, ``jc_stop`` and
``jc_subscribe``.
"""

from ..errors import JadivRuntimeError


class UnknownCommand(JadivRuntimeError):
    """The library does not know the requested command."""


class LibArgs:
    """Evaluated arguments of a library command.

    ``items`` is a list of tuples ``(kind, value[, word])`` where kind is
    ``word`` (a bare word), ``val`` (a value) or ``ref`` (``#number``).
    ``target`` holds the arguments written after ``on``.
    """

    def __init__(self, items=None, target=None):
        self.items = list(items or [])
        self.target = target

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return True

    def peek_word(self):
        """The first item as a lowercase word, or None."""
        if not self.items:
            return None
        item = self.items[0]
        if item[0] == "word":
            return item[1].lower()
        if item[0] == "val" and len(item) > 2:
            return item[2].lower()
        return None

    def take_word(self, *allowed):
        """Remove and return the first word (lowercase) if it is allowed."""
        word = self.peek_word()
        if word is None or (allowed and word not in allowed):
            return None
        self.items.pop(0)
        return word

    def take_ref(self):
        """Remove and return the first ``#number`` anywhere in the items."""
        for i, item in enumerate(self.items):
            if item[0] == "ref":
                del self.items[i]
                return item[1]
        return None

    def take_value(self, default=None):
        """Remove and return the first value (words count as text)."""
        if not self.items:
            return default
        item = self.items.pop(0)
        return item[1]

    def values(self):
        """All remaining values; bare words are returned as text."""
        out = [item[1] for item in self.items]
        self.items = []
        return out

    def take_object(self, cls):
        """Remove and return the first value that is an instance of cls."""
        for i, item in enumerate(self.items):
            if item[0] == "val" and isinstance(item[1], cls):
                del self.items[i]
                return item[1]
        return None

    def describe(self):
        parts = []
        for item in self.items:
            if item[0] == "ref":
                parts.append(f"#{item[1]}")
            elif item[0] == "word":
                parts.append(str(item[1]))
            elif len(item) > 2:
                parts.append(str(item[2]))
            else:
                parts.append(repr(item[1]))
        return " ".join(parts)


class Library:
    """Base class for every JadivCode library."""

    name = ""
    summary = ""
    #: (command, description) pairs shown by ``jadivcode libs`` and the IDE.
    commands = ()

    def __init__(self, runtime):
        self.rt = runtime

    def unknown(self, args, what="command"):
        text = args.describe()
        listing = ", ".join(c[0] for c in self.commands[:6])
        raise UnknownCommand(
            f"The {self.name} library does not understand '{text}'" if text
            else f"{self.name} needs a {what}",
            hint=f"Examples: {listing}" if listing else None, code="E350")

    def query(self, args):
        self.unknown(args)

    def statement(self, args, value):
        self.unknown(args)

    def subscribe(self, args, callback):
        raise JadivRuntimeError(f"The {self.name} library has no such event", code="E351")

    def tick(self):
        """Called often from the program thread (GUI updates)."""

    def alive(self):
        """True while the library keeps the program running."""
        return False

    def close(self):
        """Called once when the program ends."""


def _load():
    from .file import FileLibrary
    from .gpio import GPIOLibrary
    from .gui import GUILibrary
    from .mathlib import MathLibrary
    from .system import SystemLibrary
    from .timelib import TimeLibrary
    from .web import WebLibrary
    from .webui import WebUILibrary
    return {cls.name.lower(): cls for cls in (
        GPIOLibrary, MathLibrary, GUILibrary, WebUILibrary, WebLibrary,
        FileLibrary, SystemLibrary, TimeLibrary)}


class _Registry(dict):
    """Loads the library classes on first use (avoids import cycles)."""

    def _ensure(self):
        if not dict.__len__(self):
            dict.update(self, _load())

    def __contains__(self, key):
        self._ensure()
        return dict.__contains__(self, key)

    def __getitem__(self, key):
        self._ensure()
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        self._ensure()
        return dict.get(self, key, default)

    def values(self):
        self._ensure()
        return dict.values(self)

    def keys(self):
        self._ensure()
        return dict.keys(self)

    def __iter__(self):
        self._ensure()
        return dict.__iter__(self)


LIBRARIES = _Registry()
