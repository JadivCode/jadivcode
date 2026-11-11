"""Static checker: finds mistakes and risky code before a program runs.

It reports three kinds of findings:

* ``error``   - the program cannot run correctly (syntax, unknown names)
* ``warning`` - it runs, but probably not the way you want, or it is unsafe
* ``info``    - a tip

Many findings carry a ``fix`` that the CLI (``jadivcode check --fix``) and
the IDE can apply automatically.
"""

import re

from . import expr as ex
from .errors import JadivSyntaxError
from .interpreter import DURATION_RE, suggest
from .libs import LIBRARIES
from .parser import KEYWORDS, NAME_RE, first_word, parse_program

SECRET_NAME_RE = re.compile(r"(?i)(pass(word|wd)?|secret|token|api_?key|apikey|credential)")
URL_RE = re.compile(r"(?i)\bhttp://([^/\s\"'{}:]+)")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")
BUILTIN_NAMES = ("success", "error", "event")
BUILTIN_FUNCS = ("length", "count", "upper", "lower", "trim", "text", "number", "round",
                 "contains", "split", "join", "list", "json", "get", "type", "replace")
MATH_FUNCS = ("sqrt", "abs", "floor", "ceil", "min", "max", "sin", "cos", "tan", "log", "pow")


class Diagnostic:
    def __init__(self, line, severity, code, message, hint=None, fix=None):
        self.line = line or 1
        self.severity = severity
        self.code = code
        self.message = message
        self.hint = hint
        self.fix = fix

    def as_dict(self):
        data = {"line": self.line, "severity": self.severity, "code": self.code,
                "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        if self.fix:
            data["fix"] = self.fix
        return data

    def format(self, filename=None):
        where = f"{filename}:{self.line}" if filename else f"line {self.line}"
        text = f"{where}: {self.severity} {self.code}: {self.message}"
        if self.hint:
            text += f"\n  hint: {self.hint}"
        return text


class Checker:
    def __init__(self, source):
        self.source = source
        self.lines = source.splitlines()
        self.diags = []
        self.libs = set()
        self.macros = {}
        self.vars = {}        # lower -> (display, line)
        self.used = set()
        self.functions = {}
        self.tainted = {"event"}
        self.secrets = set()
        self.net_hosts = []   # (line, kind, name)
        self.protected = set()

    # -- reporting -------------------------------------------------------
    def add(self, line, severity, code, message, hint=None, fix=None):
        self.diags.append(Diagnostic(line, severity, code, message, hint, fix))

    def line_text(self, line):
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""

    def replace_fix(self, line, old, new, title):
        text = self.line_text(line)
        m = re.search(r"(?<!\w)" + re.escape(old) + r"(?!\w)", text)
        if not m:
            return None
        return {"title": title, "line": line, "text": text[:m.start()] + new + text[m.end():]}

    # -- entry -----------------------------------------------------------
    def run(self):
        nodes, errors = parse_program(self.source)
        for exc in errors:
            fix = None
            if exc.code == "E202":
                fix = {"title": "Add End", "insert_after": len(self.lines), "text": "End"}
            self.add(exc.line, "error", exc.code, exc.message, exc.hint, fix)
        self.collect_functions(nodes)
        self.block(nodes)
        self.finish()
        self.diags.sort(key=lambda d: (d.line, {"error": 0, "warning": 1, "info": 2}[d.severity]))
        return self.diags

    def collect_functions(self, nodes):
        for node in nodes:
            if node.kind == "function":
                self.functions[node.name.lower()] = node
            for attr in ("body", "else_body"):
                if isinstance(getattr(node, attr, None), list):
                    self.collect_functions(getattr(node, attr))
            if node.kind == "if":
                for _, body in node.branches:
                    self.collect_functions(body)

    def finish(self):
        for low, (name, line) in self.vars.items():
            if low not in self.used and low not in BUILTIN_NAMES:
                self.add(line, "info", "W102", f"'{name}' is defined but never used")
        for line, kind, name in self.net_hosts:
            if (kind, name) not in self.protected and (kind, None) not in self.protected:
                what = "password" if kind == "webui" else "token"
                example = "PageSet Password = {System Env \"PAGE_PASSWORD\"}" if kind == "webui" \
                    else "Set Server Token = {System Env \"HOOK_TOKEN\"}"
                self.add(line, "warning", "S003",
                         f"This opens the program to the whole network without a {what}",
                         hint=f"Anyone on the network could control it. Add: {example}")

    # -- names -----------------------------------------------------------
    def define(self, name, line):
        self.vars.setdefault(name.lower(), (name, line))

    def known_var(self, name):
        low = name.lower()
        return low in self.vars or low in BUILTIN_NAMES

    def known_func(self, name):
        low = name.lower()
        return low in self.functions or low in BUILTIN_FUNCS or \
            ("math" in self.libs and low in MATH_FUNCS)

    def all_names(self):
        return [v[0] for v in self.vars.values()] + ["Success", "Error", "Event"]

    def is_lib(self, low):
        return low in self.libs

    def is_macro(self, low):
        return low in self.macros

    # -- expressions -----------------------------------------------------
    def parse(self, source):
        return ex.Parser(ex.tokenize(source), self.is_lib, self.is_macro).parse()

    def missing_import(self, source, line):
        word = first_word(source)[0]
        low = word.lower()
        if low in LIBRARIES and low not in self.libs:
            name = LIBRARIES[low].name
            self.add(line, "error", "E330", f"The {name} library is not imported",
                     hint=f"Add 'Import {name}' at the top of the program.",
                     fix={"title": f"Import {name}", "insert_before": 1,
                          "text": f"Import {name}"})
            self.libs.add(low)
            return True
        return False

    def expr(self, source, line):
        """Check an expression that must be valid (inside { } or a condition)."""
        self.missing_import(source, line)
        try:
            node = self.parse(source)
        except JadivSyntaxError as exc:
            self.add(line, "error", "E303", f"Cannot understand '{source.strip()}': {exc.message}",
                     exc.hint)
            return
        self.walk(node, line)

    def walk(self, node, line):
        kind = node[0]
        if kind == "str":
            self.template(node[1], line)
        elif kind == "var":
            self.use(node[1], line)
        elif kind == "call":
            if not self.known_func(node[1]):
                close = suggest(node[1], [f.name for f in self.functions.values()]
                                + [b.capitalize() for b in BUILTIN_FUNCS])
                self.add(line, "error", "E320", f"There is no function called '{node[1]}'",
                         hint=f"Did you mean '{close}'?" if close else None,
                         fix=self.replace_fix(line, node[1], close, f"Change to {close}")
                         if close else None)
            for arg in node[2]:
                self.walk(arg, line)
        elif kind in ("lib", "macro"):
            if kind == "macro":
                self.used.add(node[1].lower())
            self.tokens(node[2], line)
            self.lib_rules(node[1] if kind == "lib" else self.macros[node[1].lower()],
                           node[2], None, line)
        elif kind == "index":
            self.walk(node[1], line)
            self.walk(node[2], line)
        elif kind == "un":
            self.walk(node[2], line)
        elif kind == "bin":
            self.walk(node[2], line)
            self.walk(node[3], line)

    def use(self, name, line):
        low = name.lower()
        if self.known_var(name):
            self.used.add(low)
            return
        if low in LIBRARIES:
            lib = LIBRARIES[low].name
            self.add(line, "error", "E330", f"The {lib} library is not imported",
                     hint=f"Add 'Import {lib}' at the top of the program.",
                     fix={"title": f"Import {lib}", "insert_before": 1, "text": f"Import {lib}"})
            self.libs.add(low)
            return
        close = suggest(name, self.all_names())
        self.add(line, "error", "E301", f"'{name}' is not defined",
                 hint=f"Did you mean '{close}'?" if close else
                 f"Create it first, for example: Define {name} = 0",
                 fix=self.replace_fix(line, name, close, f"Change to {close}") if close else None)

    def tokens(self, toks, line):
        """Library arguments: mark variables as used, check embedded text."""
        for tok in toks:
            if tok.kind == "ID" and tok.value.lower() in self.vars:
                self.used.add(tok.value.lower())
            elif tok.kind == "STR":
                self.template(tok.value, line)

    def template(self, text, line):
        for kind, part in ex.split_template(text):
            if kind == "expr":
                self.expr(part, line)
        m = URL_RE.search(text)
        if m and m.group(1).lower() not in LOCAL_HOSTS and not self.is_private(m.group(1)):
            self.add(line, "warning", "S002",
                     "This address uses http://, so everything is sent unencrypted",
                     hint="Use https:// when the other side supports it.",
                     fix={"title": "Use https://", "line": line,
                          "text": self.line_text(line).replace("http://", "https://")}
                     if "http://" in self.line_text(line) else None)

    @staticmethod
    def is_private(host):
        return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host)) \
            or host.endswith(".local") or "." not in host

    def value(self, raw, line):
        """Check a value after '=' (expression or text). Returns names read."""
        raw = raw.strip()
        if not raw:
            return
        word, rest = first_word(raw)
        if word.lower() == "list" and not rest.startswith("("):
            for part in self.split_args(rest):
                self.value(part, line)
            return
        self.missing_import(raw, line)
        try:
            node = self.parse(raw)
        except JadivSyntaxError:
            self.template(raw, line)
            return
        names = list(ex.names_in(node))
        unknown = [n for k, n in names if (k == "var" and not self.known_var(n))
                   or (k == "call" and not self.known_func(n))]
        if unknown:
            known = [n for k, n in names if k == "var" and self.known_var(n)]
            if node[0] == "bin" and known:
                close = suggest(unknown[0], self.all_names())
                self.add(line, "warning", "W101",
                         f"'{unknown[0]}' is not defined, so this whole value is used as plain text",
                         hint=(f"Did you mean '{close}'? " if close else "")
                         + "Put text in quotes to make that clear.",
                         fix=self.replace_fix(line, unknown[0], close, f"Change to {close}")
                         if close else None)
            self.template(raw, line)
            return
        if node[0] in ("lib", "macro") and not node[2]:
            return
        self.walk(node, line)

    def split_args(self, raw):
        raw = raw.strip()
        if raw.startswith("(") and raw.endswith(")"):
            raw = raw[1:-1]
        parts, depth, start, i = [], 0, 0, 0
        while i < len(raw):
            ch = raw[i]
            if ch == '"':
                try:
                    i = ex.scan_string(raw, i)
                    continue
                except JadivSyntaxError:
                    break
            if ch in "({":
                depth += 1
            elif ch in ")}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(raw[start:i])
                start = i + 1
            i += 1
        parts.append(raw[start:])
        return [p for p in parts if p.strip()]

    def reads(self, raw):
        """Lowercase names of variables mentioned in a raw value."""
        return {w.lower() for w in re.findall(r"[^\W\d]\w*", raw)}

    def is_tainted(self, raw):
        low = raw.lower()
        if self.reads(raw) & self.tainted:
            return True
        return bool(re.search(r"\b(web\s+(get|json|post|read)|(gui|webui)\s+read|"
                              r"file\s+(read|lines)|system\s+args)\b", low))

    # -- statements ------------------------------------------------------
    def block(self, nodes):
        for node in nodes:
            getattr(self, "c_" + node.kind, self.c_other)(node)

    def c_other(self, node):
        pass

    def c_import(self, node):
        for name in node.names:
            low = name.lower()
            if low in LIBRARIES:
                self.libs.add(low)
                continue
            close = suggest(name, [c.name for c in LIBRARIES.values()])
            self.add(node.line, "error", "E331", f"There is no library called '{name}'",
                     hint=f"Did you mean '{close}'?" if close else
                     "Libraries: " + ", ".join(c.name for c in LIBRARIES.values()),
                     fix=self.replace_fix(node.line, name, close, f"Change to {close}")
                     if close else None)
            if close:
                self.libs.add(close.lower())

    def assign(self, name, raw, line):
        low = name.lower()
        if self.is_tainted(raw):
            self.tainted.add(low)
        else:
            self.tainted.discard(low)
        self.secret_literal(name, raw, line)
        self.define(name, line)

    def secret_literal(self, name, raw, line):
        if not SECRET_NAME_RE.search(name):
            return
        self.secrets.add(name.lower())
        text = raw.strip().strip('"')
        if text and "{" not in raw and not self.known_var(text) \
                and text.lower() not in ("", "nothing", "off", "on"):
            env = re.sub(r"\W+", "_", name).upper()
            self.add(line, "warning", "S001",
                     f"A secret ('{name}') is written directly in the program",
                     hint="Anyone who can read the file can read it. Keep it outside the "
                          f"program: Define {name} = {{System Env \"{env}\"}}")

    def c_define(self, node):
        raw = node.raw
        word = first_word(raw)[0].lower()
        if "{}" in raw and word in LIBRARIES:
            self.missing_import(raw, node.line)
            at = raw.index("{}")
            try:
                prefix = ex.tokenize(raw[:at])
                suffix = ex.tokenize(raw[at + 2:])
            except JadivSyntaxError as exc:
                self.add(node.line, "error", "E303", exc.message, exc.hint)
                return
            self.tokens(prefix + suffix, node.line)
            self.macros[node.name.lower()] = prefix + [None] + suffix
            self.define(node.name, node.line)
            return
        self.value(raw, node.line)
        self.assign(node.name, raw, node.line)

    def c_set(self, node):
        self.value(node.raw, node.line)
        if node.prop is not None:
            self.use(node.name, node.line)
            self.prop_rules(node.name, node.prop, node.raw, node.line)
            return
        if not self.known_var(node.name):
            close = suggest(node.name, self.all_names())
            if close and close.lower() != node.name.lower():
                self.add(node.line, "warning", "W103",
                         f"'{node.name}' is new here but looks like '{close}'",
                         hint=f"If you meant '{close}', fix the spelling.",
                         fix=self.replace_fix(node.line, node.name, close, f"Change to {close}"))
        else:
            self.used.add(node.name.lower())
        self.assign(node.name, node.raw, node.line)

    def c_write(self, node):
        self.value(node.raw, node.line)
        leaked = self.reads(node.raw) & self.secrets
        if leaked:
            name = self.vars.get(next(iter(leaked)), (next(iter(leaked)), 0))[0]
            self.add(node.line, "warning", "S006", f"This prints the secret '{name}'",
                     hint="Secrets shown on screen end up in logs and screenshots.")

    def c_ask(self, node):
        self.value(node.raw, node.line)
        self.define(node.name, node.line)
        self.tainted.add(node.name.lower())

    def c_wait(self, node):
        raw = node.raw.strip()
        m = DURATION_RE.match(raw)
        amount = m.group(1) if m else raw
        if re.match(r"^[\d.]+\s*[^\W\d]+$", raw) and not m:
            self.add(node.line, "error", "E341", f"'{raw}' is not a valid time",
                     hint="Use ms, s, m or h. Examples: Wait 5s, Wait 200ms, Wait 2m")
            return
        self.value(amount, node.line)

    def c_if(self, node):
        for cond, body in node.branches:
            self.expr(ex.braces_to_parens(cond), node.line)
            self.block(body)
        self.block(node.else_body)

    def loop_body(self, node, endless):
        self.block(node.body)
        if endless and not self.contains(node.body, ("wait", "break", "exit", "ask", "return")):
            self.add(node.line, "warning", "W110",
                     "This loop never pauses, so it will use 100% of a CPU core",
                     hint="Add a short pause inside the loop, for example: Wait 100ms")

    def contains(self, nodes, kinds):
        for node in nodes:
            if node.kind in kinds:
                return True
            for attr in ("body", "else_body"):
                if isinstance(getattr(node, attr, None), list) and node.kind != "function" \
                        and self.contains(getattr(node, attr), kinds):
                    return True
            if node.kind == "if" and any(self.contains(b, kinds) for _, b in node.branches):
                return True
        return False

    def c_repeat(self, node):
        self.value(node.raw, node.line)
        self.loop_body(node, False)

    def c_while(self, node):
        self.expr(ex.braces_to_parens(node.raw), node.line)
        self.loop_body(node, node.raw.strip().lower() in ("on", "true", "1"))

    def c_forever(self, node):
        self.loop_body(node, True)

    def c_foreach(self, node):
        self.value(node.raw, node.line)
        self.define(node.var, node.line)
        self.used.add(node.var.lower())
        if self.is_tainted(node.raw):
            self.tainted.add(node.var.lower())
        self.loop_body(node, False)

    def c_forrange(self, node):
        for raw in (node.start, node.end, node.step):
            if raw:
                self.value(raw, node.line)
        self.define(node.var, node.line)
        self.used.add(node.var.lower())
        self.loop_body(node, False)

    def c_function(self, node):
        saved = dict(self.vars)
        for param in node.params:
            self.define(param, node.line)
            self.used.add(param.lower())
        self.block(node.body)
        added = {k: v for k, v in self.vars.items() if k not in saved}
        self.vars = saved
        self.used |= set(added)

    def c_return(self, node):
        if node.raw:
            self.value(node.raw, node.line)

    def c_call(self, node):
        func = self.functions.get(node.name.lower())
        if func is None and not self.known_func(node.name):
            close = suggest(node.name, [f.name for f in self.functions.values()])
            self.add(node.line, "error", "E320", f"There is no function called '{node.name}'",
                     hint=f"Did you mean '{close}'?" if close else None,
                     fix=self.replace_fix(node.line, node.name, close, f"Change to {close}")
                     if close else None)
        args = self.split_args(node.raw)
        for part in args:
            self.value(part, node.line)
        if func is not None and len(args) != len(func.params):
            self.add(node.line, "error", "E321",
                     f"{func.name} needs {len(func.params)} value(s) but gets {len(args)}")

    def c_on(self, node):
        word = first_word(node.raw)[0]
        low = word.lower()
        try:
            toks = ex.tokenize(node.raw)
        except JadivSyntaxError as exc:
            self.add(node.line, "error", "E303", exc.message, exc.hint)
            toks = []
        if low in self.macros:
            self.used.add(low)
        elif low in self.libs:
            pass
        elif self.known_var(word):
            self.used.add(low)
        elif not self.missing_import(node.raw, node.line):
            close = suggest(word, self.all_names())
            self.add(node.line, "error", "E346", f"'{word}' is not an event",
                     hint=(f"Did you mean '{close}'? " if close else "")
                     + "Define one first, e.g.: Define ButtonPress = GUI Read Button {} on Window #1",
                     fix=self.replace_fix(node.line, word, close, f"Change to {close}")
                     if close else None)
        self.tokens(toks[1:], node.line)
        self.define("Event", node.line)
        self.used.add("event")
        self.block(node.body)

    def c_start(self, node):
        self.use(first_word(node.raw)[0], node.line)

    c_stopobj = c_start

    def c_add(self, node):
        self.value(node.raw, node.line)
        self.use(node.name, node.line)

    c_remove = c_add

    def c_generic(self, node):
        text = node.text
        word, rest = first_word(text)
        low = word.lower()
        if low in self.macros:
            self.used.add(low)
            args_text, value_raw = ex.split_assignment(rest)
            try:
                toks = ex.tokenize(args_text)
            except JadivSyntaxError as exc:
                self.add(node.line, "error", "E303", exc.message, exc.hint)
                return
            self.tokens(toks, node.line)
            if value_raw is not None:
                self.value(value_raw, node.line)
            self.lib_rules(self.macros[low], toks, value_raw, node.line)
            return
        if low in LIBRARIES:
            self.missing_import(text, node.line)
            left, value_raw = ex.split_assignment(text)
            try:
                toks = ex.tokenize(left)
            except JadivSyntaxError as exc:
                self.add(node.line, "error", "E303", exc.message, exc.hint)
                return
            self.tokens(toks[1:], node.line)
            if value_raw is not None:
                self.value(value_raw, node.line)
            self.lib_rules(word, toks[1:], value_raw, node.line)
            return
        left, right = ex.split_assignment(text)
        if right is not None and NAME_RE.match(left) and self.known_var(left):
            self.value(right, node.line)
            self.used.add(left.lower())
            self.assign(left, right, node.line)
            return
        if low in self.functions:
            for part in self.split_args(rest):
                self.value(part, node.line)
            return
        candidates = [k.capitalize() for k in sorted(KEYWORDS)] + self.all_names() \
            + [c.name for c in LIBRARIES.values()]
        close = suggest(word, candidates) if word else None
        hint = f"Did you mean '{close}'?" if close else \
            "Every line starts with a JadivCode word such as Write, Set, Define, If or Wait."
        fix = self.replace_fix(node.line, word, close, f"Change to {close}") if close else None
        if not close and right is not None and NAME_RE.match(left):
            hint = f"To create '{left}', write: Set {left} = {right}"
            fix = {"title": "Add Set", "line": node.line,
                   "text": re.sub(r"^(\s*)", r"\1Set ", self.line_text(node.line), count=1)}
        self.add(node.line, "error", "E240", f"I do not understand '{text}'", hint, fix)

    # -- library specific rules -----------------------------------------
    def flat(self, lib_or_macro, toks):
        """Return (library name, full token list) for a call."""
        if isinstance(lib_or_macro, list):
            full = []
            for tok in lib_or_macro:
                if tok is None:
                    full.extend(toks)
                else:
                    full.append(tok)
            return full[0].value.lower(), full[1:]
        return lib_or_macro.lower(), list(toks)

    def lib_rules(self, lib_or_macro, toks, value_raw, line):
        lib, toks = self.flat(lib_or_macro, toks)
        words = [t.value.lower() for t in toks if t.kind == "ID"]
        verb = words[0] if words else ""
        strings = [t.value for t in toks if t.kind == "STR"]
        if lib == "gpio":
            self.pin_tokens(toks, line)
        if lib == "system" and verb == "run" and strings:
            command = strings[0]
            inner = [p for k, p in ex.split_template(command) if k == "expr"]
            if inner:
                risky = any(self.is_tainted(p) for p in inner)
                self.add(line, "warning" if risky else "info", "S004",
                         "A value is pasted into a command line"
                         + (" and it comes from outside the program" if risky else ""),
                         hint="A value containing spaces becomes extra options or programs. "
                              "Pass it as its own value instead: "
                              "{System Run \"ls -l\" Folder}")
        if lib == "file" and verb in ("write", "append", "delete", "read", "lines") and strings:
            inner = [p for k, p in ex.split_template(strings[0]) if k == "expr"]
            ids = [t.value for t in toks if t.kind == "ID"][1:]
            if any(self.is_tainted(p) for p in inner) or any(i.lower() in self.tainted for i in ids):
                self.add(line, "warning", "S005",
                         "The file name comes from outside the program",
                         hint="Someone could type '../../etc/passwd'. Check the name first, "
                              "for example: If Contains(Name, \"/\") then Stop")
        if lib == "web" and verb == "header" and value_raw and strings \
                and (SECRET_NAME_RE.search(strings[0])
                     or strings[0].lower() in ("authorization", "cookie")) \
                and "{" not in value_raw:
            self.add(line, "warning", "S001", "A secret is written directly in the program",
                     hint="Keep it outside the program: "
                          "Web Header \"Authorization\" = Bearer {System Env \"API_TOKEN\"}")
        if lib in ("webui", "gui") and value_raw is not None and words:
            prop = words[1] if verb == "set" and len(words) > 1 else verb
            self.surface_prop(lib, prop, value_raw, line)

    def surface_prop(self, kind, prop, raw, line, name=None):
        value = raw.strip().strip('"')
        if prop == "host" and value.lower() not in LOCAL_HOSTS:
            self.net_hosts.append((line, kind, name))
        if prop in ("password", "token"):
            self.protected.add((kind, name))
            self.protected.add((kind, None))
            if value and "{" not in raw and not self.known_var(value):
                env = "PAGE_PASSWORD" if prop == "password" else "HOOK_TOKEN"
                self.add(line, "warning", "S001",
                         f"A {prop} is written directly in the program",
                         hint=f"Keep it outside the program: {{System Env \"{env}\"}}")

    def prop_rules(self, name, prop, raw, line):
        """``Set Server Host = ...`` style properties on objects."""
        self.surface_prop("web" if prop.lower() in ("host", "token") else "webui",
                          prop.lower(), raw, line)

    def pin_tokens(self, toks, line):
        for i, tok in enumerate(toks[:-1]):
            if tok.is_op("#") and toks[i + 1].kind == "NUM":
                number = toks[i + 1].value
                if not isinstance(number, int) or not 0 <= number <= 27:
                    self.add(line, "error", "E437", f"GPIO #{number} does not exist",
                             hint="Use a BCM pin number from 0 to 27.")


def check_source(source):
    """Return a list of Diagnostic objects for the program text."""
    return Checker(source).run()


def apply_fixes(source, diagnostics):
    """Apply every available fix. Returns (new_source, number_applied)."""
    lines = source.splitlines()
    replaced = set()
    inserts_before = []
    appended = []
    count = 0
    for diag in diagnostics:
        fix = diag.fix
        if not fix:
            continue
        if "line" in fix:
            if fix["line"] in replaced or not 0 < fix["line"] <= len(lines):
                continue
            lines[fix["line"] - 1] = fix["text"]
            replaced.add(fix["line"])
            count += 1
        elif "insert_before" in fix:
            if fix["text"] not in inserts_before:
                inserts_before.append(fix["text"])
                count += 1
        elif "insert_after" in fix:
            appended.append(fix["text"])
            count += 1
    result = inserts_before + lines + appended
    return "\n".join(result) + "\n", count
