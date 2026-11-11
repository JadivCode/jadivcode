"""Math library: calculations written the way people write them."""

import math
import random
import re

from .. import expr as ex
from ..errors import JadivRuntimeError, JadivSyntaxError
from . import Library

TIMES_RE = re.compile(r"(?<=[\d)\s])[xX](?=[\s\d(])")
CONSTANTS = {"pi": math.pi, "e": math.e}


class MathLibrary(Library):
    name = "Math"
    summary = "Calculations, rounding and random numbers."
    commands = (
        ('{Math "5x5"}', "Calculate a written expression (x, ×, ÷, ^, sqrt(), sin()...)"),
        ("{Math Round 3.14159 2}", "Round to a number of decimal places"),
        ("{Math Random 1 10}", "A random whole number from 1 to 10"),
        ("{Math Sqrt 16}", "Square root"),
        ("{Math Min 4 9 2}", "Smallest value (also Max, Sum, Average)"),
        ("{Math Power 2 8}", "2 to the power of 8"),
        ("{Math Abs -5}", "Absolute value (also Floor, Ceil, Mod)"),
        ("{Math Sin 90}", "Sine of an angle in degrees (also Cos, Tan)"),
        ("{Math Pi}", "The number pi"),
    )

    def __init__(self, runtime):
        super().__init__(runtime)
        builtins = runtime.builtins
        num = self.num
        builtins.setdefault("sqrt", lambda v: self.sqrt(v))
        builtins.setdefault("abs", lambda v: abs(num(v)))
        builtins.setdefault("floor", lambda v: math.floor(num(v)))
        builtins.setdefault("ceil", lambda v: math.ceil(num(v)))
        builtins.setdefault("min", lambda *v: min(self.flat(v)))
        builtins.setdefault("max", lambda *v: max(self.flat(v)))
        builtins.setdefault("sin", lambda v: self.clean(math.sin(math.radians(num(v)))))
        builtins.setdefault("cos", lambda v: self.clean(math.cos(math.radians(num(v)))))
        builtins.setdefault("tan", lambda v: self.clean(math.tan(math.radians(num(v)))))
        builtins.setdefault("log", lambda v, base=10: self.log(v, base))
        builtins.setdefault("pow", lambda a, b: num(a) ** num(b))

    # -- helpers ---------------------------------------------------------
    def num(self, value):
        from ..interpreter import deref, to_number, to_text
        number = to_number(deref(value))
        if number is None:
            raise JadivRuntimeError(f"'{to_text(value)}' is not a number", code="E401")
        return number

    def flat(self, values):
        out = []
        for value in values:
            if isinstance(value, list):
                out.extend(self.num(v) for v in value)
            else:
                out.append(self.num(value))
        if not out:
            raise JadivRuntimeError("Math needs at least one number", code="E402")
        return out

    @staticmethod
    def clean(value):
        """Turn 25.0 into 25 and hide floating point dust."""
        value = round(value, 12)
        return int(value) if value == int(value) else value

    def sqrt(self, value):
        number = self.num(value)
        if number < 0:
            raise JadivRuntimeError("Cannot take the square root of a negative number",
                                    code="E403")
        return self.clean(math.sqrt(number))

    def log(self, value, base=10):
        number = self.num(value)
        if number <= 0:
            raise JadivRuntimeError("Log needs a number above zero", code="E404")
        return self.clean(math.log(number, self.num(base)))

    def calculate(self, text):
        source = text.replace("×", "*").replace("÷", "/").replace("·", "*")
        source = TIMES_RE.sub("*", source)
        try:
            toks = ex.tokenize(source)
            for tok in toks:
                if tok.kind == "ID" and tok.value.lower() in CONSTANTS \
                        and not self.rt.has_var(tok.value):
                    tok.kind, tok.value = "NUM", CONSTANTS[tok.value.lower()]
            node = ex.Parser(toks).parse()
        except JadivSyntaxError as exc:
            raise JadivRuntimeError(f"Math cannot understand \"{text}\": {exc.message}",
                                    hint='Example: {Math "5x5 + sqrt(16)"}', code="E405")
        result = self.rt.eval_node(node)
        if isinstance(result, float):
            return self.clean(result)
        return result

    # -- interface -------------------------------------------------------
    def query(self, args):
        verb = args.peek_word()
        if verb is None or getattr(self, "q_" + verb, None) is None:
            if verb is not None and args.items[0][0] == "word":
                self.unknown(args)
            values = args.values()
            if len(values) == 1 and isinstance(values[0], str):
                return self.calculate(values[0])
            if len(values) == 1:
                return values[0]
            self.unknown(args, "calculation")
        method = getattr(self, "q_" + verb, None)
        if method is None:
            self.unknown(args)
        args.take_word()
        return method(args.values())

    def q_pi(self, values):
        return math.pi

    def q_random(self, values):
        if not values:
            return random.random()
        if len(values) == 1 and isinstance(values[0], list):
            if not values[0]:
                raise JadivRuntimeError("Cannot pick from an empty list", code="E406")
            return random.choice(values[0])
        nums = self.flat(values)
        low, high = (1, nums[0]) if len(nums) == 1 else (nums[0], nums[1])
        if low > high:
            low, high = high, low
        if low == int(low) and high == int(high):
            return random.randint(int(low), int(high))
        return random.uniform(low, high)

    def q_round(self, values):
        nums = self.flat(values)
        digits = int(nums[1]) if len(nums) > 1 else 0
        return self.clean(round(nums[0], digits))

    def q_sqrt(self, values):
        return self.sqrt(self.flat(values)[0])

    def q_abs(self, values):
        return abs(self.flat(values)[0])

    def q_floor(self, values):
        return math.floor(self.flat(values)[0])

    def q_ceil(self, values):
        return math.ceil(self.flat(values)[0])

    def q_min(self, values):
        return min(self.flat(values))

    def q_max(self, values):
        return max(self.flat(values))

    def q_sum(self, values):
        return self.clean(sum(self.flat(values)))

    def q_average(self, values):
        nums = self.flat(values)
        return self.clean(sum(nums) / len(nums))

    def two(self, values, name):
        nums = self.flat(values)
        if len(nums) != 2:
            raise JadivRuntimeError(f"Math {name} needs two numbers", code="E407")
        return nums

    def q_power(self, values):
        a, b = self.two(values, "Power")
        return self.clean(a ** b)

    def q_mod(self, values):
        a, b = self.two(values, "Mod")
        if b == 0:
            raise JadivRuntimeError("Cannot divide by zero", code="E306")
        return a % b

    def q_sin(self, values):
        return self.clean(math.sin(math.radians(self.flat(values)[0])))

    def q_cos(self, values):
        return self.clean(math.cos(math.radians(self.flat(values)[0])))

    def q_tan(self, values):
        return self.clean(math.tan(math.radians(self.flat(values)[0])))
