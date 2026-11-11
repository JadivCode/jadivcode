"""The JadivCode runtime: executes the node tree produced by the parser."""

import difflib
import json
import queue
import re
import sys
import time

from . import expr as ex
from .errors import (BreakSignal, ContinueSignal, ExitSignal, JadivError,
                     JadivRuntimeError, JadivSyntaxError, ReturnSignal)
from .libs import LIBRARIES, LibArgs, UnknownCommand
from .parser import KEYWORDS, first_word, parse_program

DURATION_RE = re.compile(
    r"(?i)^(.*?[\d})\s])\s*(ms|milliseconds?|s|sec|seconds?|m|min|minutes?|h|hours?)$")


def suggest(word, candidates):
    match = difflib.get_close_matches(word.lower(), [c.lower() for c in candidates], n=1,
                                      cutoff=0.7)
    if not match:
        return None
    for cand in candidates:
        if cand.lower() == match[0]:
            return cand
    return match[0]


def to_number(value):
    """Convert a value to a number or return None."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip().replace(",", ".") if value.count(",") == 1 and "." not in value \
            else value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return None
    return None


def deref(value):
    """Library objects (pins, windows...) act as their current value."""
    getter = getattr(value, "jc_get", None)
    return getter() if getter else value


def to_text(value):
    value = deref(value)
    if value is None:
        return "Nothing"
    if value is True:
        return "ON"
    if value is False:
        return "OFF"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return repr(round(value, 10))
    if isinstance(value, list):
        return ", ".join(to_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=to_text)
    return str(value)


def truthy(value):
    value = deref(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("", "off", "false", "no", "0", "nothing")
    return bool(value)


def loosely_equal(a, b):
    a, b = deref(a), deref(b)
    if isinstance(a, bool) or isinstance(b, bool):
        return truthy(a) == truthy(b)
    na, nb = to_number(a), to_number(b)
    if na is not None and nb is not None:
        return na == nb
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


class Runtime:
    """Holds all program state and runs statements."""

    def __init__(self, out=None, inp=None, err=None, filename=None, strict=False,
                 args=None):
        self.out = out or (lambda text: print(text, flush=True))
        self.err = err or (lambda text: print(text, file=sys.stderr, flush=True))
        self.inp = inp or input
        self.filename = filename
        self.strict = strict
        self.args = list(args or [])
        self.globals = {}
        self.frames = []
        self.names = {}
        self.functions = {}
        self.macros = {}
        self.libs = {}
        self.events = queue.Queue()
        self.error_count = 0
        self.last_failed = False
        self.last_error = ""
        self.line = None
        self.stopping = False
        self._ast_cache = {}
        self.builtins = self._make_builtins()

    # ------------------------------------------------------------------
    # Running programs
    # ------------------------------------------------------------------
    def run_source(self, source):
        nodes, errors = parse_program(source)
        if errors:
            raise errors[0]
        return self.run_nodes(nodes)

    def run_nodes(self, nodes):
        code = 0
        try:
            self.exec_block(nodes)
            self.event_loop()
        except ExitSignal as sig:
            code = sig.code
        except (BreakSignal, ContinueSignal, ReturnSignal):
            pass
        except KeyboardInterrupt:
            code = 130
        finally:
            self.shutdown()
        return code

    def shutdown(self):
        self.stopping = True
        for lib in self.libs.values():
            try:
                lib.close()
            except Exception:  # noqa: BLE001 - closing must never fail the program
                pass

    def exec_block(self, nodes):
        for node in nodes:
            self.exec_node(node)

    def exec_node(self, node):
        self.line = node.line
        try:
            getattr(self, "do_" + node.kind)(node)
            self.last_failed = False
        except JadivError as exc:
            if exc.line is None:
                exc.line = node.line
            if isinstance(exc, JadivSyntaxError) or self.strict:
                raise
            self.soft_error(exc)

    def soft_error(self, exc):
        self.error_count += 1
        self.last_failed = True
        self.last_error = exc.message
        self.err("warning: " + exc.format(self.filename))

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def post(self, callback, *args):
        """Thread-safe: schedule ``callback(*args)`` on the program thread."""
        self.events.put((callback, args))

    def pump(self, seconds=0.0):
        """Process GUI updates and queued events for ``seconds``."""
        deadline = time.monotonic() + max(0.0, seconds)
        if seconds > 0:
            for lib in list(self.libs.values()):
                lib.ready()
        while True:
            for lib in list(self.libs.values()):
                lib.tick()
            while True:
                try:
                    callback, args = self.events.get_nowait()
                except queue.Empty:
                    break
                callback(*args)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.01, remaining))

    def alive(self):
        return any(lib.alive() for lib in self.libs.values())

    def event_loop(self):
        """After the last line: keep running while windows/servers are open."""
        for lib in list(self.libs.values()):
            lib.ready()
        while self.alive():
            self.pump(0.05)
        self.pump(0)

    def make_handler(self, node):
        def handler(value=None):
            saved = self.line
            self.set_var("Event", value)
            try:
                self.exec_block(node.body)
            except ReturnSignal:
                pass
            finally:
                self.line = saved
        return handler

    # ------------------------------------------------------------------
    # Names
    # ------------------------------------------------------------------
    def is_lib(self, low):
        return low in self.libs

    def is_macro(self, low):
        return low in self.macros

    def has_var(self, name):
        low = name.lower()
        if low in ("success", "error"):
            return True
        if self.frames and low in self.frames[-1]:
            return True
        return low in self.globals

    def get_var(self, name):
        low = name.lower()
        if self.frames and low in self.frames[-1]:
            return self.frames[-1][low]
        if low in self.globals:
            return self.globals[low]
        if low == "success":
            if self.error_count == 0:
                return "Full"
            return "None" if self.last_failed else "Partial"
        if low == "error":
            return self.last_error
        hint = None
        close = suggest(name, list(self.names.values()) + ["Success", "Error"])
        if close:
            hint = f"Did you mean '{close}'?"
        raise JadivRuntimeError(f"'{name}' is not defined", hint=hint, code="E301")

    def set_var(self, name, value):
        low = name.lower()
        self.names.setdefault(low, name)
        if self.frames and (low in self.frames[-1] or low not in self.globals):
            self.frames[-1][low] = value
        else:
            self.globals[low] = value

    # ------------------------------------------------------------------
    # Expressions
    # ------------------------------------------------------------------
    def parse_expr(self, source):
        key = (source, len(self.libs), len(self.macros))
        node = self._ast_cache.get(key)
        if node is None:
            toks = ex.tokenize(source)
            node = ex.Parser(toks, self.is_lib, self.is_macro).parse()
            self._ast_cache[key] = node
        return node

    def eval_expr(self, source):
        return self.eval_node(self.parse_expr(source))

    def eval_condition(self, source):
        try:
            return truthy(self.eval_expr(ex.braces_to_parens(source)))
        except JadivSyntaxError as exc:
            raise JadivRuntimeError(f"Cannot understand the condition '{source}': {exc.message}",
                                    hint=exc.hint, code="E302")

    def eval_template(self, raw):
        """Text with {values}; a lone {value} keeps its kind (number, list...)."""
        parts = ex.split_template(raw)
        if len(parts) == 1 and parts[0][0] == "expr":
            try:
                return self.eval_expr(parts[0][1])
            except JadivSyntaxError as exc:
                raise JadivRuntimeError(
                    f"Cannot understand '{raw}': {exc.message}", hint=exc.hint, code="E303")
        return self.eval_text(raw)

    def eval_text(self, template):
        out = []
        for kind, part in ex.split_template(template):
            if kind == "text":
                out.append(part)
            else:
                try:
                    out.append(to_text(self.eval_expr(part)))
                except JadivSyntaxError as exc:
                    raise JadivRuntimeError(
                        f"Cannot understand '{{{part}}}': {exc.message}", hint=exc.hint,
                        code="E303")
        return "".join(out)

    def eval_value(self, raw):
        """Value after '=': an expression when it is one, otherwise text."""
        raw = raw.strip()
        if raw == "":
            return ""
        word, rest = first_word(raw)
        if word.lower() == "list" and not rest.startswith("("):
            return self.eval_arg_list(rest)
        try:
            node = self.parse_expr(raw)
        except JadivSyntaxError:
            return self.eval_template(raw)
        for kind, name in ex.names_in(node):
            low = name.lower()
            if kind == "var" and not self.has_var(name):
                return self.eval_template(raw)
            if kind == "call" and low not in self.functions and low not in self.builtins:
                return self.eval_template(raw)
        if node[0] in ("lib", "macro"):
            if not node[2]:
                return self.eval_template(raw)
            try:
                return self.eval_node(node)
            except UnknownCommand:
                # Plain text that merely starts with a library name.
                return self.eval_template(raw)
        return self.eval_node(node)

    def eval_node(self, node):
        kind = node[0]
        if kind in ("num", "const"):
            return node[1]
        if kind == "str":
            return self.eval_text(node[1])
        if kind == "var":
            return self.get_var(node[1])
        if kind == "call":
            return self.call_function(node[1], [self.eval_node(a) for a in node[2]])
        if kind == "lib":
            return self.lib_query([ex.Tok("ID", node[1], 0)] + node[2])
        if kind == "macro":
            return self.lib_query(self.expand_macro(node[1], node[2]))
        if kind == "index":
            return self.index(self.eval_node(node[1]), self.eval_node(node[2]))
        if kind == "un":
            value = deref(self.eval_node(node[2]))
            if node[1] == "not":
                return not truthy(value)
            number = to_number(value)
            if number is None:
                raise JadivRuntimeError(f"Cannot negate '{to_text(value)}'", code="E304")
            return -number
        if kind == "bin":
            return self.binary(node[1], node[2], node[3])
        raise JadivRuntimeError("Internal error: unknown expression", code="E399")

    def binary(self, op, left_node, right_node):
        if op == "and":
            return truthy(self.eval_node(left_node)) and truthy(self.eval_node(right_node))
        if op == "or":
            return truthy(self.eval_node(left_node)) or truthy(self.eval_node(right_node))
        a = deref(self.eval_node(left_node))
        b = deref(self.eval_node(right_node))
        if op == "=":
            return loosely_equal(a, b)
        if op == "!=":
            return not loosely_equal(a, b)
        if op == "==":
            return a == b
        na, nb = to_number(a), to_number(b)
        if op in ("<", ">", "<=", ">="):
            if na is None or nb is None:
                na, nb = to_text(a).lower(), to_text(b).lower()
            return {"<": na < nb, ">": na > nb, "<=": na <= nb, ">=": na >= nb}[op]
        if op == "+":
            if isinstance(a, list):
                return a + (b if isinstance(b, list) else [b])
            if na is None or nb is None or (isinstance(a, str) and isinstance(b, str)
                                            and not (a.strip() and b.strip())):
                return to_text(a) + to_text(b)
            return na + nb
        if na is None or nb is None:
            bad = a if na is None else b
            raise JadivRuntimeError(
                f"Cannot calculate with '{to_text(bad)}' because it is not a number",
                code="E305")
        if op == "-":
            return na - nb
        if op == "*":
            return na * nb
        if op in ("/", "%"):
            if nb == 0:
                raise JadivRuntimeError("Cannot divide by zero", code="E306")
            if op == "%":
                return na % nb
            result = na / nb
            return int(result) if result == int(result) else result
        if op == "^":
            try:
                return na ** nb
            except (OverflowError, ZeroDivisionError):
                raise JadivRuntimeError("The result is too large", code="E307")
        raise JadivRuntimeError(f"Unknown operator '{op}'", code="E399")

    def index(self, base, key):
        base, key = deref(base), deref(key)
        if isinstance(base, dict):
            for k, v in base.items():
                if str(k).lower() == to_text(key).lower():
                    return v
            return None
        number = to_number(key)
        if isinstance(base, (list, str)):
            if number is None or int(number) != number:
                raise JadivRuntimeError(f"'#{to_text(key)}' is not a position number",
                                        code="E308")
            pos = int(number)
            if pos < 1 or pos > len(base):
                raise JadivRuntimeError(
                    f"Position #{pos} does not exist (there are {len(base)} items)",
                    hint="Positions start at #1.", code="E309")
            return base[pos - 1]
        raise JadivRuntimeError(f"'{to_text(base)}' has no numbered items", code="E310")

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------
    def _make_builtins(self):
        def need_list(value, name):
            value = deref(value)
            if not isinstance(value, list):
                raise JadivRuntimeError(f"{name} needs a list", code="E311")
            return value

        def b_length(value):
            value = deref(value)
            if isinstance(value, (list, dict)):
                return len(value)
            return len(to_text(value))

        def b_number(value):
            number = to_number(deref(value))
            if number is None:
                raise JadivRuntimeError(f"'{to_text(value)}' is not a number", code="E312")
            return number

        def b_round(value, digits=0):
            result = round(b_number(value), int(b_number(digits)))
            return int(result) if result == int(result) else result

        def b_contains(haystack, needle):
            haystack = deref(haystack)
            if isinstance(haystack, list):
                return any(loosely_equal(item, needle) for item in haystack)
            if isinstance(haystack, dict):
                return any(str(k).lower() == to_text(needle).lower() for k in haystack)
            return to_text(needle).lower() in to_text(haystack).lower()

        def b_json(text):
            try:
                return json.loads(to_text(text))
            except ValueError:
                raise JadivRuntimeError("The text is not valid JSON", code="E313")

        def b_type(value):
            value = deref(value)
            if value is None:
                return "Nothing"
            if isinstance(value, bool):
                return "Switch"
            if isinstance(value, (int, float)):
                return "Number"
            if isinstance(value, list):
                return "List"
            if isinstance(value, dict):
                return "Map"
            return "Text"

        return {
            "length": b_length,
            "count": b_length,
            "upper": lambda v: to_text(v).upper(),
            "lower": lambda v: to_text(v).lower(),
            "trim": lambda v: to_text(v).strip(),
            "text": to_text,
            "number": b_number,
            "round": b_round,
            "contains": b_contains,
            "split": lambda v, sep=",": [p.strip() for p in to_text(v).split(to_text(sep))],
            "join": lambda v, sep=", ": to_text(sep).join(to_text(i) for i in need_list(v, "Join")),
            "list": lambda *items: list(items),
            "json": b_json,
            "get": lambda base, key: self.index(base, key),
            "type": b_type,
            "replace": lambda v, old, new: to_text(v).replace(to_text(old), to_text(new)),
        }

    def call_function(self, name, args):
        low = name.lower()
        func = self.functions.get(low)
        if func is None:
            builtin = self.builtins.get(low)
            if builtin is None:
                close = suggest(name, [f.name for f in self.functions.values()]
                                + [b.capitalize() for b in self.builtins])
                raise JadivRuntimeError(f"There is no function called '{name}'",
                                        hint=f"Did you mean '{close}'?" if close else None,
                                        code="E320")
            try:
                return builtin(*args)
            except TypeError:
                raise JadivRuntimeError(f"{name} was given the wrong number of values",
                                        code="E321")
        if len(args) != len(func.params):
            raise JadivRuntimeError(
                f"{func.name} needs {len(func.params)} value(s) but got {len(args)}",
                code="E321")
        if len(self.frames) > 200:
            raise JadivRuntimeError(f"{func.name} calls itself too many times", code="E322")
        frame = {p.lower(): a for p, a in zip(func.params, args)}
        for param in func.params:
            self.names.setdefault(param.lower(), param)
        self.frames.append(frame)
        saved_line = self.line
        try:
            self.exec_block(func.body)
        except ReturnSignal as sig:
            return sig.value
        finally:
            self.frames.pop()
            self.line = saved_line
        return None

    def eval_arg_list(self, raw):
        """Comma separated values for ``Call Name a, b``."""
        raw = raw.strip()
        if raw.startswith("(") and raw.endswith(")"):
            raw = raw[1:-1].strip()
        if not raw:
            return []
        parts = []
        start = 0
        while True:
            at = ex.find_top_level(raw, ",", start)
            if at == -1:
                parts.append(raw[start:])
                break
            # Commas inside parentheses belong to nested calls.
            piece = raw[start:at]
            if piece.count("(") != piece.count(")"):
                nxt = ex.find_top_level(raw, ",", at + 1)
                if nxt == -1:
                    parts.append(raw[start:])
                    break
                at = nxt
            parts.append(raw[start:at])
            start = at + 1
        return [self.eval_value(p) for p in parts]

    # ------------------------------------------------------------------
    # Libraries
    # ------------------------------------------------------------------
    def get_lib(self, name):
        low = name.lower()
        lib = self.libs.get(low)
        if lib is None:
            if low in LIBRARIES:
                raise JadivRuntimeError(
                    f"The {LIBRARIES[low].name} library is not imported",
                    hint=f"Add 'Import {LIBRARIES[low].name}' at the top of the program.",
                    code="E330")
            raise JadivRuntimeError(f"There is no library called '{name}'", code="E331")
        return lib

    def build_args(self, toks):
        """Turn raw tokens into evaluated library arguments."""
        items = []
        target = None
        i = 0
        n = len(toks)
        while i < n:
            tok = toks[i]
            if tok.kind == "NUM":
                items.append(("val", tok.value))
            elif tok.kind == "STR":
                items.append(("val", self.eval_text(tok.value)))
            elif tok.kind == "ID":
                low = tok.value.lower()
                if low == "on" and i > 0 and i + 1 < n and not toks[i + 1].is_op("="):
                    target = self.build_args(toks[i + 1:])
                    break
                if low in ex.TRUE_WORDS:
                    items.append(("val", True))
                elif low in ex.FALSE_WORDS:
                    items.append(("val", False))
                elif self.has_var(tok.value) and low not in ("success", "error"):
                    items.append(("val", self.get_var(tok.value), tok.value))
                else:
                    items.append(("word", tok.value))
            elif tok.is_op("#"):
                if i + 1 >= n:
                    raise JadivRuntimeError("'#' needs a number after it", code="E332")
                nxt = toks[i + 1]
                if nxt.kind == "NUM":
                    items.append(("ref", nxt.value))
                elif nxt.kind == "ID" and self.has_var(nxt.value):
                    items.append(("ref", deref(self.get_var(nxt.value))))
                elif nxt.kind == "STR":
                    items.append(("ref", self.eval_text(nxt.value)))
                else:
                    raise JadivRuntimeError(f"'#{nxt.value}' is not a number", code="E332")
                i += 1
            elif tok.is_op("("):
                depth = 1
                j = i + 1
                while j < n and depth:
                    if toks[j].is_op("("):
                        depth += 1
                    elif toks[j].is_op(")"):
                        depth -= 1
                    j += 1
                inner = toks[i + 1:j - 1]
                node = ex.Parser(inner, self.is_lib, self.is_macro).parse()
                items.append(("val", self.eval_node(node)))
                i = j - 1
            elif tok.is_op("-") and i + 1 < n and toks[i + 1].kind == "NUM":
                items.append(("val", -toks[i + 1].value))
                i += 1
            elif tok.is_op(","):
                pass
            else:
                raise JadivRuntimeError(f"Unexpected '{tok.value}' in a library command",
                                        code="E333")
            i += 1
        return LibArgs(items, target)

    def lib_query(self, toks):
        lib = self.get_lib(toks[0].value)
        return lib.query(self.build_args(toks[1:]))

    def lib_statement(self, toks, value_raw):
        lib = self.get_lib(toks[0].value)
        args = self.build_args(toks[1:])
        value = self.eval_value(value_raw) if value_raw is not None else None
        lib.statement(args, value)

    def expand_macro(self, name, arg_toks):
        prefix, suffix = self.macros[name.lower()]
        return prefix + list(arg_toks) + suffix

    # ------------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------------
    def do_import(self, node):
        for name in node.names:
            low = name.lower()
            if low in self.libs:
                continue
            cls = LIBRARIES.get(low)
            if cls is None:
                close = suggest(name, [c.name for c in LIBRARIES.values()])
                raise JadivSyntaxError(
                    f"There is no library called '{name}'", line=node.line, code="E331",
                    hint=f"Did you mean '{close}'?" if close else
                    "Run 'jadivcode libs' to see every library.")
            self.libs[low] = cls(self)

    def do_define(self, node):
        raw = node.raw
        word = first_word(raw)[0].lower()
        if "{}" in raw and (word in self.libs or word in LIBRARIES):
            self.get_lib(word)
            at = raw.index("{}")
            self.macros[node.name.lower()] = (ex.tokenize(raw[:at]), ex.tokenize(raw[at + 2:]))
            self.names.setdefault(node.name.lower(), node.name)
            return
        value = self.eval_value(raw)
        named = getattr(value, "jc_named", None)
        if named is not None:
            named(node.name)
        self.set_var(node.name, value)

    def do_set(self, node):
        value = self.eval_value(node.raw)
        if node.prop is not None:
            target = self.get_var(node.name)
            setter = getattr(target, "jc_set_prop", None)
            if setter is None:
                raise JadivRuntimeError(f"'{node.name}' has no properties to set", code="E340")
            setter(node.prop, value)
            return
        if self.has_var(node.name):
            current = self.get_var(node.name)
            setter = getattr(current, "jc_set", None)
            if setter is not None:
                setter(deref(value))
                return
        self.set_var(node.name, value)

    def do_write(self, node):
        self.out(to_text(self.eval_value(node.raw)))

    def do_ask(self, node):
        prompt = to_text(self.eval_value(node.raw)) if node.raw else ""
        try:
            answer = self.inp(prompt + " " if prompt else "")
        except EOFError:
            answer = ""
        number = to_number(answer)
        self.set_var(node.name, number if number is not None and answer.strip() else answer)

    def parse_duration(self, raw):
        m = DURATION_RE.match(raw.strip())
        if m:
            amount_raw, unit = m.group(1), m.group(2).lower()
        else:
            amount_raw, unit = raw, "s"
        amount = to_number(deref(self.eval_value(amount_raw)))
        if amount is None or amount < 0:
            raise JadivRuntimeError(f"'{raw}' is not a valid time", code="E341",
                                    hint="Examples: Wait 5s, Wait 200ms, Wait 2m")
        if unit in ("ms",) or unit.startswith("mil"):
            factor = 0.001
        elif unit.startswith("h"):
            factor = 3600
        elif unit.startswith("m"):
            factor = 60
        else:
            factor = 1
        return amount * factor

    def do_wait(self, node):
        self.pump(self.parse_duration(node.raw))

    def do_if(self, node):
        for cond, body in node.branches:
            if self.eval_condition(cond):
                self.exec_block(body)
                return
        self.exec_block(node.else_body)

    def run_loop_body(self, body):
        """Run one loop round. Returns False when the loop must stop."""
        try:
            self.exec_block(body)
        except BreakSignal:
            return False
        except ContinueSignal:
            pass
        self.pump(0)
        return True

    def do_repeat(self, node):
        count = to_number(deref(self.eval_value(node.raw)))
        if count is None:
            raise JadivRuntimeError(f"Repeat needs a number, not '{node.raw}'", code="E342")
        for _ in range(int(count)):
            if not self.run_loop_body(node.body):
                break

    def do_while(self, node):
        while self.eval_condition(node.raw):
            if not self.run_loop_body(node.body):
                break

    def do_forever(self, node):
        while True:
            if not self.run_loop_body(node.body):
                break

    def do_foreach(self, node):
        items = deref(self.eval_value(node.raw))
        if isinstance(items, dict):
            items = list(items.keys())
        elif isinstance(items, str):
            items = list(items)
        elif not isinstance(items, list):
            raise JadivRuntimeError(f"'{node.raw}' is not a list", code="E343")
        for item in list(items):
            self.set_var(node.var, item)
            if not self.run_loop_body(node.body):
                break

    def do_forrange(self, node):
        start = to_number(deref(self.eval_value(node.start)))
        end = to_number(deref(self.eval_value(node.end)))
        step = to_number(deref(self.eval_value(node.step))) if node.step else None
        if start is None or end is None or (node.step and step is None):
            raise JadivRuntimeError("For needs numbers: For I = 1 to 10", code="E344")
        if step is None:
            step = 1 if end >= start else -1
        if step == 0:
            raise JadivRuntimeError("The step of a For loop cannot be 0", code="E344")
        value = start
        while (step > 0 and value <= end) or (step < 0 and value >= end):
            self.set_var(node.var, value)
            if not self.run_loop_body(node.body):
                break
            value += step

    def do_function(self, node):
        self.functions[node.name.lower()] = node

    def do_return(self, node):
        if not self.frames:
            raise JadivRuntimeError("Return can only be used inside a Function", code="E345")
        raise ReturnSignal(self.eval_value(node.raw) if node.raw else None)

    def do_call(self, node):
        self.call_function(node.name, self.eval_arg_list(node.raw))

    def do_on(self, node):
        handler = self.make_handler(node)

        def callback(value=None):
            self.post(self.run_handler, handler, value)

        raw = node.raw
        word = first_word(raw)[0]
        low = word.lower()
        toks = ex.tokenize(raw)
        if low in self.macros:
            toks = self.expand_macro(word, toks[1:])
            low = toks[0].value.lower()
        if low in self.libs:
            self.libs[low].subscribe(self.build_args(toks[1:]), callback)
            return
        if self.has_var(word):
            subscribe = getattr(self.get_var(word), "jc_subscribe", None)
            if subscribe is not None:
                subscribe(self.build_args(toks[1:]), callback)
                return
            raise JadivRuntimeError(f"'{word}' does not produce events", code="E346")
        if low in LIBRARIES:
            self.get_lib(word)
        raise JadivRuntimeError(
            f"'{word}' is not an event", code="E346",
            hint="Define one first, e.g.: Define ButtonPress = GUI Read Button {} on Window #1")

    def run_handler(self, handler, value):
        try:
            handler(value)
        except JadivError as exc:
            if isinstance(exc, JadivSyntaxError) or self.strict:
                raise
            self.soft_error(exc)

    def object_action(self, raw, action):
        name = first_word(raw)[0]
        target = self.get_var(name)
        method = getattr(target, action, None)
        if method is None:
            verb = "started" if action == "jc_start" else "stopped"
            raise JadivRuntimeError(f"'{name}' cannot be {verb}", code="E347")
        method()

    def do_start(self, node):
        self.object_action(node.raw, "jc_start")

    def do_stopobj(self, node):
        self.object_action(node.raw, "jc_stop")

    def do_exit(self, node):
        raise ExitSignal(0)

    def do_break(self, node):
        raise BreakSignal()

    def do_continue(self, node):
        raise ContinueSignal()

    def do_strict(self, node):
        self.strict = node.on

    def get_list(self, name):
        value = self.get_var(name)
        if not isinstance(value, list):
            raise JadivRuntimeError(f"'{name}' is not a list", code="E343",
                                    hint=f"Create one with: Define {name} = List 1, 2, 3")
        return value

    def do_add(self, node):
        self.get_list(node.name).append(self.eval_value(node.raw))

    def do_remove(self, node):
        items = self.get_list(node.name)
        value = self.eval_value(node.raw)
        for i, item in enumerate(items):
            if loosely_equal(item, value):
                del items[i]
                return

    def do_generic(self, node):
        text = node.text
        word, rest = first_word(text)
        low = word.lower()
        if low in self.macros:
            args_text, value_raw = ex.split_assignment(rest)
            toks = self.expand_macro(word, ex.tokenize(args_text))
            self.lib_statement(toks, value_raw)
            return
        if low in self.libs or low in LIBRARIES:
            left, value_raw = ex.split_assignment(text)
            self.get_lib(word)
            self.lib_statement(ex.tokenize(left), value_raw)
            return
        left, right = ex.split_assignment(text)
        if right is not None and re.match(r"^[^\W\d]\w*$", left):
            if self.has_var(left):
                setter = getattr(self.get_var(left), "jc_set", None)
                if setter is not None:
                    setter(deref(self.eval_value(right)))
                    return
                self.set_var(left, self.eval_value(right))
                return
        if low in self.functions:
            self.call_function(word, self.eval_arg_list(rest))
            return
        candidates = ([k.capitalize() for k in KEYWORDS] + list(self.names.values())
                      + [c.name for c in LIBRARIES.values()])
        close = suggest(word, candidates) if word else None
        raise JadivSyntaxError(
            f"I do not understand '{text}'", line=node.line, code="E240",
            hint=f"Did you mean '{close}'?" if close else
            "Every line starts with a JadivCode word such as Write, Set, Define, If or Wait.")


def run_file(path, strict=False, args=None, out=None, err=None, inp=None):
    """Run a .jdv file. Returns the process exit code."""
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    runtime = Runtime(filename=path, strict=strict, args=args, out=out, err=err, inp=inp)
    try:
        return runtime.run_source(source)
    except JadivError as exc:
        runtime.err("error: " + exc.format(path))
        return 1
