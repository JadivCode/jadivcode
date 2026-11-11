"""Widget model shared by the GUI and WebUI libraries.

Both libraries understand the same commands, so a program can move from a
desktop window to a web page by changing only the library name::

    <Lib> Write <Kind> ["X = 10, Y = 10"] "<id>" on <Target> = text
    <Lib> Set   <Property>                 on <Target> = value
    <Lib> Set   "<id>" <Property>          on <Target> = value
    <Lib> Read  <Kind> ["<id>"]            on <Target>
    <Lib> Remove "<id>"                    on <Target>
    <Lib> Press "<id>"                     on <Target>
"""

import re

from ..errors import JadivRuntimeError

KINDS = ("text", "button", "input", "checkbox", "slider", "image")
POSITION_RE = re.compile(r"^\s*[xy]\s*[=:]?\s*-?\d", re.I)
COORD_RE = re.compile(r"\b([xywh])\s*[=:]?\s*(-?\d+)", re.I)


def parse_position(text):
    """'X = 10, Y 10, W 80' -> {'x': 10, 'y': 10, 'w': 80}."""
    return {m.group(1).lower(): int(m.group(2)) for m in COORD_RE.finditer(text)}


class Widget:
    def __init__(self, wid, kind):
        self.id = wid
        self.kind = kind
        self.text = ""
        self.value = False if kind == "checkbox" else (0 if kind == "slider" else "")
        self.pos = {}
        self.props = {}

    def as_dict(self):
        return {"id": self.id, "kind": self.kind, "text": self.text,
                "value": self.value, "pos": self.pos, "props": self.props}


SURFACE_PROPS = ("title", "size", "width", "height", "color", "background", "port", "host",
                 "password")


class Item:
    """A named item: ``Define Hello = GUI Text "Hello" at 20, 40``.

    It behaves like any other thing in the language: ``Set Hello = ...``
    changes it, ``{Hello}`` reads it, ``On Hello`` reacts to it and
    ``Stop Hello`` removes it.
    """

    def __init__(self, surface, widget):
        self.surface = surface
        self.widget = widget

    def __repr__(self):
        return f"{self.widget.kind} \"{self.widget.id}\""

    def jc_named(self, name):
        """Called by ``Define``: the item takes the name as its id."""
        self.surface.rename(self.widget, name)

    def jc_get(self):
        if self.widget.kind in ("input", "checkbox", "slider"):
            return self.widget.value
        return self.widget.text

    def jc_set(self, value):
        prop = "value" if self.widget.kind in ("input", "checkbox", "slider") else "text"
        self.surface.set_widget(self.widget.id, prop, value)

    def jc_set_prop(self, name, value):
        self.surface.set_widget(self.widget.id, name, value)

    def jc_subscribe(self, args, callback):
        self.surface.listen(self.widget.kind, self.widget.id, callback)

    def jc_stop(self):
        if self.widget.id.lower() in self.surface.widgets:
            self.surface.remove(self.widget.id)


class Surface:
    """A window or web page: a set of widgets plus event listeners."""

    label = "window"

    def __init__(self, lib, number):
        self.lib = lib
        self.number = number
        self.props = {"title": "JadivCode", "width": 400, "height": 300}
        self.widgets = {}
        self.order = []
        self.listeners = []
        self.last = {}
        self.version = 0
        self.started = False
        self.manual = False
        self.auto = 0

    def __repr__(self):
        return f"{self.lib.name} {self.label.capitalize()} #{self.number}"

    # -- hooks for the concrete surface ---------------------------------
    def render(self, widget=None, removed=None):
        """Called after every change of the model."""

    def show(self):
        pass

    def hide(self):
        pass

    # -- language object interface --------------------------------------
    def jc_get(self):
        return self.started

    def jc_start(self):
        self.manual = True
        self.started = True
        self.show()

    def jc_stop(self):
        self.manual = True
        self.started = False
        self.hide()

    def autostart(self):
        """Show a surface that has items but was never started by hand."""
        if not self.manual and not self.started and self.widgets:
            self.jc_start()

    def rename(self, widget, name):
        old, new = widget.id.lower(), name.lower()
        if old == new or new in self.widgets:
            return
        hidden = Widget(widget.id, widget.kind)
        self.widgets[new] = self.widgets.pop(old)
        self.order[self.order.index(old)] = new
        self.listeners = [(k, new if t == old else t, c) for k, t, c in self.listeners]
        widget.id = name
        self.touch()
        self.render(removed=hidden)
        self.render(widget)

    def jc_set_prop(self, name, value):
        self.set_prop(name, value)

    # -- model -----------------------------------------------------------
    def touch(self):
        self.version += 1

    def set_prop(self, name, value):
        from ..interpreter import deref, to_number, to_text
        value = deref(value)
        low = name.lower()
        if low == "size":
            m = re.match(r"^\s*(\d+)\s*[x, ]\s*(\d+)\s*$", to_text(value), re.I)
            if not m:
                raise JadivRuntimeError("Size is written like 400x300", code="E440")
            self.props["width"], self.props["height"] = int(m.group(1)), int(m.group(2))
        elif low in ("width", "height", "port"):
            number = to_number(value)
            if number is None:
                raise JadivRuntimeError(f"{name} needs a number", code="E441")
            self.props[low] = int(number)
        else:
            self.props[low] = value if isinstance(value, bool) else to_text(value)
        self.touch()
        self.render()

    def widget(self, wid):
        widget = self.widgets.get(str(wid).lower())
        if widget is None:
            raise JadivRuntimeError(
                f"There is no item \"{wid}\" on {self!r}", code="E442",
                hint="Create it first with Write, for example: Write Text \"T01\" = Hello")
        return widget

    def write(self, kind, strings, value):
        from ..interpreter import deref, to_number, to_text
        pos = None
        wid = None
        for text in strings:
            if isinstance(text, str) and POSITION_RE.match(text) and pos is None:
                pos = parse_position(text)
            elif wid is None:
                wid = to_text(text)
        if wid is None:
            self.auto += 1
            wid = f"{kind}{self.auto}"
        key = wid.lower()
        widget = self.widgets.get(key)
        if widget is None:
            widget = Widget(wid, kind)
            self.widgets[key] = widget
            self.order.append(key)
        elif widget.kind != kind:
            raise JadivRuntimeError(
                f"\"{wid}\" already exists as a {widget.kind}", code="E443",
                hint="Give every item its own id.")
        if pos is not None:
            widget.pos = pos
        value = deref(value)
        if value is not None:
            if kind == "slider":
                number = to_number(value)
                if number is None:
                    raise JadivRuntimeError("A slider needs a number", code="E444")
                widget.value = number
            elif kind == "input":
                widget.value = to_text(value)
            else:
                widget.text = to_text(value)
        self.touch()
        self.render(widget)
        return widget

    def set_widget(self, wid, prop, value):
        from ..interpreter import deref, to_number, to_text, truthy
        widget = self.widget(wid)
        value = deref(value)
        low = prop.lower()
        if low == "text":
            widget.text = to_text(value)
        elif low == "value":
            if widget.kind == "checkbox":
                widget.value = truthy(value)
            elif widget.kind == "slider":
                widget.value = to_number(value) or 0
            else:
                widget.value = to_text(value)
        elif low in ("x", "y", "w", "h", "width", "height"):
            number = to_number(value)
            if number is None:
                raise JadivRuntimeError(f"{prop} needs a number", code="E441")
            widget.pos[low[0]] = int(number)
        else:
            widget.props[low] = value if isinstance(value, (bool, int, float)) else to_text(value)
        self.touch()
        self.render(widget)

    def remove(self, wid):
        widget = self.widget(wid)
        key = widget.id.lower()
        del self.widgets[key]
        self.order.remove(key)
        self.touch()
        self.render(removed=widget)

    def read(self, kind, wid):
        if wid is not None:
            widget = self.widget(wid)
            if widget.kind in ("input", "checkbox", "slider"):
                return widget.value
            if widget.kind == "button":
                return self.last.get("button") == widget.id
            return widget.text
        return self.last.get(kind)

    def listen(self, kind, wid, callback):
        self.listeners.append((kind, wid.lower() if wid is not None else None, callback))

    def fire(self, wid, value=None):
        """An event from the user: a click, an edit, a toggle..."""
        widget = self.widgets.get(str(wid).lower())
        if widget is None:
            return
        if widget.kind in ("input", "checkbox", "slider") and value is not None:
            widget.value = value
            self.touch()
        event = widget.id if widget.kind == "button" else widget.value
        self.last[widget.kind] = event
        for kind, target, callback in list(self.listeners):
            if kind == widget.kind and target in (None, widget.id.lower()):
                callback(event)

    def snapshot(self):
        return {"version": self.version, "props": self.props,
                "widgets": [self.widgets[k].as_dict() for k in self.order]}


class SurfaceLibrary:
    """Mixin implementing the shared widget commands."""

    surface_class = Surface
    surface_word = "window"

    def init_surfaces(self):
        self.surfaces = {}

    def surface(self, args, create=False):
        obj = args.take_object(Surface)
        if obj is not None:
            return obj
        args.take_word(self.surface_word)
        number = args.take_ref()
        if number is None:
            if self.surfaces and not create:
                return self.first_surface()
            number = 1
        surface = self.surfaces.get(number)
        if surface is None:
            if not create and self.surfaces:
                raise JadivRuntimeError(
                    f"{self.name} {self.surface_word.capitalize()} #{number} is not defined",
                    code="E445",
                    hint=f"Example: Define My{self.surface_word.capitalize()} = {self.name} "
                         f"{self.surface_word.capitalize()} #{number}")
            surface = self.surface_class(self, number)
            self.surfaces[number] = surface
        return surface

    def first_surface(self):
        """The default window or page: #1, or the first one that was made."""
        return self.surfaces.get(1) or next(iter(self.surfaces.values()))

    def target(self, args):
        if args.target is not None:
            return self.surface(args.target)
        if self.surfaces:
            return self.first_surface()
        return self.surface(args, create=True)

    def kind(self, args, verb):
        kind = args.take_word(*KINDS)
        if kind is None:
            word = args.peek_word()
            raise JadivRuntimeError(
                f"{self.name} {verb} needs a kind of item"
                + (f", not '{word}'" if word else ""), code="E447",
                hint="Kinds: Text, Button, Input, Checkbox, Slider, Image.")
        return kind

    def surface_query(self, args):
        verb = args.peek_word()
        if verb == self.surface_word:
            args.take_word()
            return self.surface(args, create=True)
        if verb == "read":
            args.take_word()
            surface = self.target(args)
            kind = self.kind(args, "Read")
            values = args.values()
            return surface.read(kind, values[0] if values else None)
        if verb in KINDS:
            return self.make_item(args)
        self.unknown(args)

    def make_item(self, args):
        """``GUI Button "Press me" at 20, 60 size 100, 30 [on Window #2]``."""
        from ..interpreter import to_number
        kind = args.take_word()
        surface = self.target(args)
        value = None
        pos = {}
        while args.items:
            word = args.peek_word()
            if word in ("at", "size"):
                args.take_word()
                numbers = []
                while args.items and len(numbers) < 2 and args.peek_word() not in ("at", "size"):
                    number = to_number(args.take_value())
                    if number is None:
                        break
                    numbers.append(int(number))
                if len(numbers) != 2:
                    raise JadivRuntimeError(
                        f"'{word}' needs two numbers", code="E449",
                        hint='Example: GUI Button "OK" at 20, 60 size 100, 30')
                keys = ("x", "y") if word == "at" else ("w", "h")
                pos[keys[0]], pos[keys[1]] = numbers
            elif value is None:
                value = args.take_value()
            else:
                raise JadivRuntimeError(
                    f"Unexpected '{args.describe()}' after the {kind}", code="E449",
                    hint='Example: GUI Button "OK" at 20, 60 size 100, 30')
        widget = surface.write(kind, [], value)
        if pos:
            widget.pos = pos
            surface.touch()
            surface.render(widget)
        return Item(surface, widget)

    def autostart(self):
        for surface in list(self.surfaces.values()):
            surface.autostart()

    def surface_statement(self, args, value):
        from ..interpreter import to_text
        verb = args.take_word()
        if verb == "write":
            surface = self.target(args)
            kind = self.kind(args, "Write")
            surface.write(kind, args.values(), value)
            return
        if verb == "set":
            surface = self.target(args)
            first = args.items[0] if args.items else None
            if first is not None and first[0] == "val" and len(first) == 2 \
                    and isinstance(first[1], str):
                wid = args.take_value()
                prop = args.take_word() or "text"
                surface.set_widget(wid, prop, value)
                return
            prop = args.take_word()
            if prop is None:
                raise JadivRuntimeError(f"{self.name} Set needs a property name", code="E448",
                                        hint="Example: Set Title = My app")
            surface.set_prop(prop, value)
            return
        if verb == "remove":
            self.target(args).remove(to_text(args.take_value("")))
            return
        if verb == "press":
            surface = self.target(args)
            wid = to_text(args.take_value(""))
            widget = surface.widget(wid)
            # Pressing a checkbox toggles it; an input or slider reports its value.
            surface.fire(wid, (not widget.value) if widget.kind == "checkbox" else None)
            return
        if verb in ("start", "show"):
            self.target(args).jc_start()
            return
        if verb in ("stop", "hide", "close"):
            self.target(args).jc_stop()
            return
        if verb in SURFACE_PROPS and value is not None:
            self.target(args).set_prop(verb, value)
            return
        self.unknown(args)

    def surface_subscribe(self, args, callback):
        from ..interpreter import to_text
        args.take_word("read")
        surface = self.target(args)
        kind = self.kind(args, "Read")
        values = args.values()
        surface.listen(kind, to_text(values[0]) if values else None, callback)
