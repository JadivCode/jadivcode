"""GUI library: desktop windows with buttons, text, inputs and more.

Windows are drawn with Tk (``python3-tk``).  Without a display, or with
``JADIVCODE_GUI=headless``, windows are simulated so programs still run -
useful for tests and for servers.
"""

import os

from ..errors import JadivRuntimeError
from . import Library
from .widgets import Surface, SurfaceLibrary


def load_tk():
    if os.environ.get("JADIVCODE_GUI", "").strip().lower() == "headless":
        return None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    try:
        import tkinter
        return tkinter
    except ImportError:
        return None


class Window(Surface):
    label = "window"

    def __init__(self, lib, number):
        super().__init__(lib, number)
        self.top = None
        self.tk_widgets = {}
        self.tk_vars = {}
        self.images = {}
        self.updating = False

    # -- lifecycle -------------------------------------------------------
    def show(self):
        tk = self.lib.tk
        if tk is None:
            self.lib.say(f"window #{self.number} opened: {self.props['title']}")
            return
        if self.top is not None:
            return
        root = self.lib.root_window()
        self.top = tk.Toplevel(root)
        self.top.protocol("WM_DELETE_WINDOW", self.jc_stop)
        self.apply_props()
        for key in self.order:
            self.draw(self.widgets[key])

    def hide(self):
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:  # noqa: BLE001 - the window may already be gone
                pass
            self.top = None
            self.tk_widgets = {}
            self.tk_vars = {}

    def apply_props(self):
        self.top.title(str(self.props.get("title", "JadivCode")))
        self.top.geometry(f"{self.props['width']}x{self.props['height']}")
        color = self.props.get("color") or self.props.get("background")
        if color:
            self.safe(lambda: self.top.configure(bg=color), f"'{color}' is not a color")

    def safe(self, action, message):
        try:
            action()
        except Exception:  # noqa: BLE001 - Tk raises TclError for bad options
            raise JadivRuntimeError(message, code="E450")

    # -- drawing ---------------------------------------------------------
    def render(self, widget=None, removed=None):
        if self.lib.tk is None:
            if widget is not None and self.started:
                shown = widget.text if widget.kind not in ("input", "slider") else widget.value
                self.lib.say(f"window #{self.number} {widget.kind} \"{widget.id}\": {shown}")
            return
        if self.top is None:
            return
        if removed is not None:
            old = self.tk_widgets.pop(removed.id.lower(), None)
            if old is not None:
                old.destroy()
            return
        if widget is None:
            self.apply_props()
        else:
            self.draw(widget)

    def draw(self, widget):
        tk = self.lib.tk
        key = widget.id.lower()
        item = self.tk_widgets.get(key)
        self.updating = True
        try:
            if item is None:
                item = self.create(tk, widget)
                self.tk_widgets[key] = item
            if widget.kind in ("text", "button", "checkbox"):
                item.configure(text=widget.text)
            if widget.kind == "image" and widget.text:
                try:
                    self.images[key] = tk.PhotoImage(file=os.path.expanduser(widget.text))
                except Exception:  # noqa: BLE001
                    raise JadivRuntimeError(
                        f"Cannot show the image '{widget.text}'", code="E451",
                        hint="Use a PNG or GIF file that exists.")
                item.configure(image=self.images[key])
            var = self.tk_vars.get(key)
            if var is not None and var.get() != widget.value:
                var.set(widget.value)
            self.style(item, widget)
            pos = widget.pos
            if "x" in pos or "y" in pos:
                place = {"x": pos.get("x", 0), "y": pos.get("y", 0)}
                if "w" in pos:
                    place["width"] = pos["w"]
                if "h" in pos:
                    place["height"] = pos["h"]
                item.place(**place)
            else:
                item.pack(anchor="w", padx=10, pady=4)
        finally:
            self.updating = False

    def style(self, item, widget):
        props = widget.props
        options = {}
        if props.get("color"):
            options["fg"] = props["color"]
        if props.get("background"):
            options["bg"] = props["background"]
        if props.get("size"):
            try:
                options["font"] = ("TkDefaultFont", int(float(props["size"])))
            except (TypeError, ValueError):
                raise JadivRuntimeError("Size needs a number", code="E441")
        if "enabled" in props and widget.kind != "text":
            from ..interpreter import truthy
            options["state"] = "normal" if truthy(props["enabled"]) else "disabled"
        if options:
            self.safe(lambda: item.configure(**options),
                      f"Cannot apply the look of \"{widget.id}\" (check the color name)")

    def create(self, tk, widget):
        wid = widget.id
        key = wid.lower()
        if widget.kind == "button":
            return tk.Button(self.top, command=lambda: self.fire(wid))
        if widget.kind == "input":
            var = tk.StringVar(value=widget.value)
            self.tk_vars[key] = var
            var.trace_add("write", lambda *_: self.sync(wid, var))
            entry = tk.Entry(self.top, textvariable=var)
            entry.bind("<Return>", lambda _event: self.fire(wid, var.get()))
            return entry
        if widget.kind == "checkbox":
            var = tk.BooleanVar(value=bool(widget.value))
            self.tk_vars[key] = var
            return tk.Checkbutton(self.top, variable=var,
                                  command=lambda: self.fire(wid, bool(var.get())))
        if widget.kind == "slider":
            var = tk.DoubleVar(value=widget.value)
            self.tk_vars[key] = var
            low = float(widget.props.get("min", 0))
            high = float(widget.props.get("max", 100))
            return tk.Scale(self.top, from_=low, to=high, orient="horizontal", variable=var,
                            command=lambda _v: self.slide(wid, var))
        return tk.Label(self.top, anchor="w", justify="left")

    def sync(self, wid, var):
        """Keep the model up to date while the user types."""
        widget = self.widgets.get(wid.lower())
        if widget is not None and not self.updating:
            widget.value = var.get()

    def slide(self, wid, var):
        if not self.updating:
            value = var.get()
            self.fire(wid, int(value) if value == int(value) else value)


class GUILibrary(SurfaceLibrary, Library):
    name = "GUI"
    summary = "Desktop windows with buttons, text, inputs, checkboxes and sliders."
    surface_class = Window
    surface_word = "window"
    commands = (
        ("GUI Title = My app", "Window properties: Title, Size, Width, Height, Color"),
        ("GUI Size = 400x300", "Window size in pixels"),
        ('Define Hello = GUI Text "Hello" at 20, 20', "A text"),
        ('Define Go = GUI Button "Press me" at 20, 60', "A button"),
        ("Define Field = GUI Input at 20, 100 size 200, 28", "A box to type in"),
        ('Define Agree = GUI Checkbox "I agree" at 20, 140', "A checkbox"),
        ("Define Level = GUI Slider 50 at 20, 180", "A slider from 0 to 100"),
        ('Define Logo = GUI Image "logo.png" at 20, 230', "A PNG or GIF picture"),
        ("Set Hello = New text", "Change the text or value of an item"),
        ("Set Hello Color = red", "Item properties: Text, Value, Color, Background, Size, "
                                  "Enabled, X, Y, W, H, Min, Max"),
        ("{Field}", "What the item holds (typed text, slider position, ON/OFF)"),
        ('On Go "Write = pressed"', "Run a statement on a click, Enter, toggle or move"),
        ("Stop Hello", "Remove an item"),
        ('GUI Press "Go"', "Click an item from the program"),
        ("Define Second = GUI Window #2", "Another window; add 'on Window #2' to its items"),
        ("Start Second", "Show a window now (Stop Second closes it)"),
        ('GUI Write Button "X = 10, Y = 10" "B01" on Window #1 = Press me',
         "Long form: create or change an item by id"),
        ('GUI Set "B01" Color on Window #1 = red', "Long form: change a property by id"),
        ('{GUI Read Input "I01" on Window #1}', "Long form: read an item by id"),
        ('On GUI Read Button "B01" on Window #1 "Write = pressed"', "Long form: an event by id"),
        ('GUI Remove "B01" on Window #1', "Long form: remove an item by id"),
        ("Define WindowWrite = GUI Write {} on Window #1", "A shortcut for a long form"),
    )

    def __init__(self, runtime):
        super().__init__(runtime)
        self.init_surfaces()
        self.tk = load_tk()
        self.root = None
        self.announced = False

    def say(self, text):
        if not self.announced:
            self.announced = True
            self.rt.out("[GUI simulator] no display found, windows are simulated")
        self.rt.out(f"[GUI simulator] {text}")

    def root_window(self):
        if self.root is None:
            try:
                self.root = self.tk.Tk()
            except Exception as exc:  # noqa: BLE001
                raise JadivRuntimeError(f"Cannot open a window: {exc}", code="E452")
            self.root.withdraw()
        return self.root

    def query(self, args):
        return self.surface_query(args)

    def statement(self, args, value):
        self.surface_statement(args, value)

    def subscribe(self, args, callback):
        self.surface_subscribe(args, callback)

    def ready(self):
        self.autostart()

    def tick(self):
        if self.root is not None:
            try:
                self.root.update()
            except Exception:  # noqa: BLE001 - window destroyed while updating
                pass

    def alive(self):
        return self.tk is not None and any(w.top is not None for w in self.surfaces.values())

    def close(self):
        for window in self.surfaces.values():
            window.hide()
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:  # noqa: BLE001
                pass
            self.root = None
