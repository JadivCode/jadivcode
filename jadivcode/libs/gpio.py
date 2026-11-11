"""GPIO library: switch pins on a Raspberry Pi, or simulate them on a PC.

Pins use BCM numbering (the "GPIO17" numbers, not the physical pin
positions).  On a computer without GPIO hardware every pin is simulated
and each change is printed, so programs can be written and tested anywhere.

Set the environment variable ``JADIVCODE_GPIO`` to ``sim`` to force the
simulator or to ``real`` to refuse to run without hardware.
"""

import os

from ..errors import JadivRuntimeError
from . import Library

MIN_PIN = 0
MAX_PIN = 27


def load_backend():
    """Return the gpiozero module when real pins are usable, else None."""
    mode = os.environ.get("JADIVCODE_GPIO", "auto").strip().lower()
    if mode == "sim":
        return None
    try:
        import gpiozero
        from gpiozero import Device
        ensure = getattr(Device, "ensure_pin_factory", None)
        if ensure is not None:
            ensure()
        elif Device.pin_factory is None:
            Device.pin_factory = Device._default_pin_factory()
        return gpiozero
    except Exception as exc:  # noqa: BLE001 - any failure means "no hardware"
        if mode == "real":
            raise JadivRuntimeError(
                f"GPIO hardware is not available: {exc}", code="E430",
                hint="Install it with: sudo apt install python3-gpiozero python3-lgpio")
        return None


class Pin:
    """One GPIO pin. Its direction is chosen by how it is first used."""

    def __init__(self, lib, number, mode=None, pull=None):
        self.lib = lib
        self.number = number
        self.mode = mode
        self.pull = pull
        self.state = False
        self.level = 0
        self.device = None
        self.listeners = []

    def __repr__(self):
        return f"GPIO #{self.number}"

    # -- setup -----------------------------------------------------------
    def ensure(self, mode):
        if self.mode is None:
            self.mode = mode
        if self.mode == "pwm" and mode == "output":
            mode = "pwm"
        if self.mode != mode:
            raise JadivRuntimeError(
                f"GPIO #{self.number} is used as {self.mode}, so it cannot be used as {mode}",
                code="E431")
        if self.device is not None or self.lib.backend is None:
            return
        zero = self.lib.backend
        try:
            if mode == "output":
                self.device = zero.DigitalOutputDevice(self.number)
            elif mode == "pwm":
                self.device = zero.PWMOutputDevice(self.number)
            else:
                pull_up = {"up": True, "down": False}.get(self.pull)
                kwargs = {"pull_up": pull_up}
                if pull_up is None:
                    kwargs["active_state"] = True
                self.device = zero.DigitalInputDevice(self.number, **kwargs)
                self.device.when_activated = lambda: self.changed(True)
                self.device.when_deactivated = lambda: self.changed(False)
        except Exception as exc:  # noqa: BLE001 - hardware errors vary by driver
            raise JadivRuntimeError(f"Cannot use GPIO #{self.number}: {exc}", code="E432")

    def changed(self, state):
        """Called (maybe from another thread) when an input changes."""
        self.state = state
        for edge, callback in list(self.listeners):
            if edge == "change" or (edge == "rising") == state:
                callback(state)

    # -- language interface ---------------------------------------------
    def jc_get(self):
        if self.mode == "pwm":
            return self.level
        if self.mode == "input" and self.device is not None:
            return bool(self.device.value)
        return self.state

    def jc_set(self, value):
        from ..interpreter import to_number, truthy
        if self.mode == "input":
            raise JadivRuntimeError(f"GPIO #{self.number} is an input and cannot be set",
                                    code="E433")
        if self.mode == "pwm":
            self.ensure("pwm")
            number = to_number(value)
            if isinstance(value, bool) or number is None:
                number = 100 if truthy(value) else 0
            if not 0 <= number <= 100:
                raise JadivRuntimeError("A PWM pin takes a value from 0 to 100", code="E434")
            self.level = number
            self.state = number > 0
            if self.device is not None:
                self.device.value = number / 100
            else:
                self.lib.say(f"pin {self.number} -> {number:g}%")
            return
        self.ensure("output")
        self.state = truthy(value)
        if self.device is not None:
            self.device.value = 1 if self.state else 0
        else:
            self.lib.say(f"pin {self.number} -> {'ON' if self.state else 'OFF'}")

    def jc_subscribe(self, args, callback):
        self.ensure("input")
        word = args.take_word() or "pressed"
        edge = {"pressed": "rising", "rising": "rising", "on": "rising",
                "released": "falling", "falling": "falling", "off": "falling",
                "change": "change", "changed": "change"}.get(word)
        if edge is None:
            raise JadivRuntimeError(
                f"'{word}' is not a pin event", code="E435",
                hint="Use Pressed, Released or Changed.")
        self.listeners.append((edge, callback))

    def jc_stop(self):
        self.listeners = []
        if self.device is not None:
            self.device.close()
            self.device = None


class GPIOLibrary(Library):
    name = "GPIO"
    summary = "Switch and read the pins of a Raspberry Pi (simulated on a PC)."
    commands = (
        ("Define Lamp = GPIO #17", "A pin; it becomes an output when you Set it"),
        ("Define Button = GPIO Input #4", "An input pin (add PullUp or PullDown)"),
        ("Define Fan = GPIO PWM #18", "A dimmable output, set it from 0 to 100"),
        ("Set Lamp = ON", "Switch a pin (ON / OFF)"),
        ("{Lamp}", "The current state of a pin"),
        ("GPIO Write #17 = ON", "Switch a pin without defining it"),
        ("{GPIO Read #4}", "Read a pin without defining it"),
        ('On Button Pressed "Write = click"', "Run a statement when an input changes"),
        ("GPIO Simulate #4 = ON", "Simulator only: pretend an input changed"),
        ("{GPIO Mode}", '"real" on a Raspberry Pi, "sim" in the simulator'),
    )

    def __init__(self, runtime):
        super().__init__(runtime)
        self.backend = load_backend()
        self.pins = {}
        self.announced = False

    def say(self, text):
        if not self.announced:
            self.announced = True
            self.rt.out("[GPIO simulator] no GPIO hardware found, pins are simulated")
        self.rt.out(f"[GPIO simulator] {text}")

    def pin(self, args, mode=None):
        number = args.take_ref()
        if number is None:
            obj = args.take_object(Pin)
            if obj is not None:
                return obj
            raise JadivRuntimeError("GPIO needs a pin number", code="E436",
                                    hint="Example: GPIO #17")
        if isinstance(number, bool) or not isinstance(number, int) \
                or not MIN_PIN <= number <= MAX_PIN:
            raise JadivRuntimeError(
                f"GPIO #{number} does not exist", code="E437",
                hint=f"Use a BCM pin number from {MIN_PIN} to {MAX_PIN}.")
        pull = args.take_word("pullup", "pulldown")
        pin = self.pins.get(number)
        if pin is None:
            pin = Pin(self, number, mode, pull[4:] if pull else None)
            self.pins[number] = pin
        elif mode is not None and pin.mode is None:
            pin.mode = mode
        elif mode is not None and pin.mode != mode:
            raise JadivRuntimeError(
                f"GPIO #{number} is already used as {pin.mode}", code="E431")
        return pin

    def query(self, args):
        verb = args.peek_word()
        if verb in ("input", "output", "pwm"):
            args.take_word()
            pin = self.pin(args, verb)
            pin.ensure(verb)
            return pin
        if verb == "read":
            args.take_word()
            pin = self.pin(args)
            if pin.mode is None:
                pin.ensure("input")
            return pin.jc_get()
        if verb == "mode":
            return "real" if self.backend is not None else "sim"
        if verb is None:
            return self.pin(args)
        self.unknown(args)

    def statement(self, args, value):
        verb = args.take_word()
        if verb == "write":
            self.pin(args).jc_set(value)
            return
        if verb == "simulate":
            from ..interpreter import truthy
            pin = self.pin(args)
            if self.backend is not None:
                raise JadivRuntimeError("GPIO Simulate only works in the simulator",
                                        code="E438")
            if pin.mode is None:
                pin.mode = "input"
            state = truthy(value)
            if state != pin.state:
                self.say(f"input {pin.number} -> {'ON' if state else 'OFF'}")
                pin.changed(state)
            return
        if verb == "cleanup":
            self.close()
            return
        self.unknown(args)

    def subscribe(self, args, callback):
        args.take_word("read")
        self.pin(args).jc_subscribe(args, callback)

    def alive(self):
        return self.backend is not None and any(p.listeners for p in self.pins.values())

    def close(self):
        for pin in self.pins.values():
            pin.jc_stop()
