"""Statement parser: turns JadivCode source text into a tree of nodes.

JadivCode is line based.  Every line is one statement; blocks start with a
keyword line (``If``, ``Repeat``, ``While``, ``Forever``, ``For``,
``Function``, ``On``) and finish with ``End``.
"""

import re

from .errors import JadivSyntaxError
from .expr import find_top_level, scan_string, split_assignment

KEYWORDS = {
    "import", "define", "set", "write", "ask", "wait", "if", "else", "end",
    "repeat", "while", "forever", "for", "function", "return", "call", "on",
    "start", "stop", "exit", "break", "continue", "strict", "add", "remove",
}

# Keywords that may begin a statement written inside quotes after
# ``then`` / ``else`` / ``On ...``.
INLINE_KEYWORDS = KEYWORDS - {"else", "end", "function", "on", "repeat",
                              "while", "forever", "for", "import"}

NAME_RE = re.compile(r"^[^\W\d]\w*$")
WORD_RE = re.compile(r"^\s*([^\W\d]\w*)(.*)$", re.S)


class Node:
    """One parsed statement."""

    def __init__(self, kind, line, text, **fields):
        self.kind = kind
        self.line = line
        self.text = text
        self.__dict__.update(fields)

    def __repr__(self):
        extra = {k: v for k, v in self.__dict__.items()
                 if k not in ("kind", "line", "text")}
        return f"Node({self.kind}@{self.line} {extra})"


def strip_comment(line):
    """Remove comments: a line starting with '#', or '//' after whitespace."""
    stripped = line.strip()
    if stripped.startswith("#") and not re.match(r"#\s*\d", stripped):
        return ""
    if stripped.startswith("//"):
        return ""
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            try:
                i = scan_string(line, i)
            except JadivSyntaxError:
                return line.strip()
            continue
        if ch == "/" and line[i:i + 2] == "//" and i > 0 and line[i - 1].isspace():
            return line[:i].strip()
        i += 1
    return line.strip()


def first_word(text):
    m = WORD_RE.match(text)
    if not m:
        return "", text.strip()
    return m.group(1), m.group(2).strip()


def unquote(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].strip()
    return text


def looks_like_statement(text):
    """True when ``text`` could be a statement (used for quoted statements)."""
    m = re.match(r"^([^\W\d]\w*)(\s|=|$)", text)
    if not m:
        return False
    if m.group(1).lower() in INLINE_KEYWORDS:
        return True
    _, right = split_assignment(text)
    return right is not None


def find_word(text, word, start=0, last=False):
    """Find a whole word outside quotes (case-insensitive). -1 if absent."""
    pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.I)
    found = -1
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            try:
                i = scan_string(text, i)
            except JadivSyntaxError:
                break
            continue
        m = pattern.match(text, i)
        if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            if not last:
                return i
            found = i
            i = m.end()
            continue
        i += 1
    return found


def split_quoted_statement(text):
    """Split ``<head> "<statement>"`` where the statement may contain quotes.

    Returns (head, statement) or (text, None) when no quoted statement ends
    the text.
    """
    text = text.rstrip()
    if not text.endswith('"'):
        return text, None
    end = len(text) - 1
    for i, ch in enumerate(text[:end]):
        if ch == '"' and looks_like_statement(text[i + 1:end].strip()):
            return text[:i].strip(), text[i + 1:end].strip()
    return text, None


class ProgramParser:
    def __init__(self, source):
        self.lines = []
        for number, raw in enumerate(source.splitlines(), start=1):
            text = strip_comment(raw)
            if text:
                self.lines.append((number, text))
        self.pos = 0
        self.errors = []

    # -- public ----------------------------------------------------------
    def parse(self):
        body = self.parse_block(top=True)
        return body

    # -- block handling --------------------------------------------------
    def parse_block(self, top=False, opener=None):
        """Parse statements until End / Else (not consumed for Else)."""
        body = []
        while self.pos < len(self.lines):
            number, text = self.lines[self.pos]
            word = first_word(text)[0].lower()
            if word == "end":
                if top:
                    self.error("'End' without a block to close", number, code="E201",
                               hint="Remove this End, or check that the block above "
                                    "starts with If, Repeat, While, Forever, For, "
                                    "Function or On.")
                    self.pos += 1
                    continue
                return body
            if word == "else" and not top and opener is not None and opener.kind == "if":
                return body
            self.pos += 1
            try:
                node = self.parse_line(text, number)
            except JadivSyntaxError as exc:
                if exc.line is None:
                    exc.line = number
                self.errors.append(exc)
                continue
            if node is not None:
                body.append(node)
        if not top and opener is not None:
            self.error(f"'{opener.text}' is never closed with End", opener.line,
                       code="E202", hint="Add a line with End after the block.")
        return body

    def close_block(self):
        """Consume the End line of a block if present."""
        if self.pos < len(self.lines):
            word = first_word(self.lines[self.pos][1])[0].lower()
            if word == "end":
                self.pos += 1

    def error(self, message, line, hint=None, code="E200"):
        self.errors.append(JadivSyntaxError(message, line=line, hint=hint, code=code))

    # -- statements ------------------------------------------------------
    def parse_line(self, text, line, inline=False):
        word, rest = first_word(text)
        low = word.lower()
        handler = getattr(self, "st_" + low, None) if low in KEYWORDS else None
        if handler is None:
            return Node("generic", line, text)
        if inline and low not in INLINE_KEYWORDS:
            raise JadivSyntaxError(f"'{word}' cannot be used inside a one-line statement",
                                   line=line, code="E203",
                                   hint="Write it as a block on its own lines.")
        return handler(rest, line, text, inline)

    def parse_inline(self, text, line):
        return self.parse_line(unquote(text), line, inline=True)

    def st_import(self, rest, line, text, inline):
        names = [n.strip() for n in re.split(r"[,\s]+", rest) if n.strip()]
        if not names:
            raise JadivSyntaxError("Import needs a library name", line=line, code="E210",
                                   hint="Example: Import Math")
        for name in names:
            if not NAME_RE.match(name):
                raise JadivSyntaxError(f"'{name}' is not a valid library name",
                                       line=line, code="E210")
        return Node("import", line, text, names=names)

    def st_define(self, rest, line, text, inline):
        left, right = split_assignment(rest)
        if right is None or not NAME_RE.match(left):
            raise JadivSyntaxError("Define needs a name and a value", line=line,
                                   code="E211", hint="Example: Define Lamp = GPIO #17")
        self.check_name(left, line)
        return Node("define", line, text, name=left, raw=right)

    def st_set(self, rest, line, text, inline):
        left, right = split_assignment(rest)
        if right is None:
            raise JadivSyntaxError("Set needs '=' and a value", line=line, code="E212",
                                   hint="Example: Set Lamp = ON")
        parts = left.split()
        if not parts or len(parts) > 2 or not all(NAME_RE.match(p) for p in parts):
            raise JadivSyntaxError(f"'{left}' is not something that can be set",
                                   line=line, code="E212",
                                   hint="Use: Set Name = value   or   Set Name Property = value")
        if len(parts) == 1:
            self.check_name(parts[0], line)
        return Node("set", line, text, name=parts[0],
                    prop=parts[1] if len(parts) == 2 else None, raw=right)

    def st_write(self, rest, line, text, inline):
        if rest.startswith("="):
            rest = rest[1:].strip()
        return Node("write", line, text, raw=rest)

    def st_ask(self, rest, line, text, inline):
        left, right = split_assignment(rest)
        if not NAME_RE.match(left):
            raise JadivSyntaxError("Ask needs a name to store the answer in", line=line,
                                   code="E213", hint="Example: Ask Name = What is your name?")
        self.check_name(left, line)
        return Node("ask", line, text, name=left, raw=right or "")

    def st_wait(self, rest, line, text, inline):
        if not rest:
            raise JadivSyntaxError("Wait needs a time", line=line, code="E214",
                                   hint="Example: Wait 5s   (ms, s, m and h are allowed)")
        return Node("wait", line, text, raw=rest)

    def st_if(self, rest, line, text, inline):
        then_at = find_word(rest, "then")
        if then_at != -1 and rest[then_at + 4:].strip():
            cond = rest[:then_at].strip()
            tail = rest[then_at + 4:].strip()
            then_text, else_text = self.split_else(tail)
            then_node = self.parse_inline(then_text, line)
            else_node = self.parse_inline(else_text, line) if else_text else None
            self.need(cond, "If needs a condition", line, "Example: If Count > 3 then Write = big")
            return Node("if", line, text, branches=[(cond, [then_node])],
                        else_body=[else_node] if else_node else [])
        if inline:
            raise JadivSyntaxError("A one-line If needs 'then'", line=line, code="E215")
        cond = rest[:then_at].strip() if then_at != -1 else rest
        self.need(cond, "If needs a condition", line, "Example: If Count > 3")
        node = Node("if", line, text, branches=[], else_body=[])
        node.branches.append((cond, self.parse_block(opener=node)))
        while self.pos < len(self.lines):
            number, ltext = self.lines[self.pos]
            word, lrest = first_word(ltext)
            if word.lower() != "else":
                break
            self.pos += 1
            sub_word, sub_rest = first_word(lrest)
            if sub_word.lower() == "if":
                sub_cond = sub_rest
                at = find_word(sub_cond, "then")
                if at != -1:
                    sub_cond = sub_cond[:at].strip()
                self.need(sub_cond, "Else If needs a condition", number, "Example: Else If Count > 3")
                node.branches.append((sub_cond, self.parse_block(opener=node)))
            elif lrest:
                self.error("Nothing may follow Else on the same line in a block If",
                           number, code="E216", hint="Put the statement on the next line.")
                node.else_body = self.parse_block(opener=node)
            else:
                node.else_body = self.parse_block(opener=node)
        self.close_block()
        return node

    def split_else(self, tail):
        """Split the part after ``then`` into (then_text, else_text)."""
        low = tail.lower()
        positions = [m.start() for m in re.finditer(r"(?<!\w)else(?!\w)", low)]
        for at in positions:
            left, right = tail[:at].strip(), tail[at + 4:].strip()
            if not left or not right:
                continue
            if left.startswith('"') != left.endswith('"'):
                continue
            if left.startswith('"') or find_word(tail, "else") == at:
                return left, right
        return tail, None

    def need(self, value, message, line, hint):
        if not value:
            raise JadivSyntaxError(message, line=line, code="E215", hint=hint)

    def st_else(self, rest, line, text, inline):
        raise JadivSyntaxError("'Else' without a matching If", line=line, code="E217")

    def st_end(self, rest, line, text, inline):
        raise JadivSyntaxError("'End' without a block to close", line=line, code="E201")

    def block(self, node):
        node.body = self.parse_block(opener=node)
        self.close_block()
        return node

    def st_repeat(self, rest, line, text, inline):
        count = re.sub(r"(?i)\s*times?\s*$", "", rest).strip()
        if not count:
            raise JadivSyntaxError("Repeat needs a number", line=line, code="E218",
                                   hint="Example: Repeat 5 times")
        return self.block(Node("repeat", line, text, raw=count))

    def st_while(self, rest, line, text, inline):
        if not rest:
            raise JadivSyntaxError("While needs a condition", line=line, code="E219",
                                   hint="Example: While Count < 10")
        return self.block(Node("while", line, text, raw=rest))

    def st_forever(self, rest, line, text, inline):
        return self.block(Node("forever", line, text))

    def st_for(self, rest, line, text, inline):
        m = re.match(r"(?i)^each\s+([^\W\d]\w*)\s+in\s+(.+)$", rest)
        if m:
            self.check_name(m.group(1), line)
            return self.block(Node("foreach", line, text, var=m.group(1), raw=m.group(2)))
        left, right = split_assignment(rest)
        if right is not None and NAME_RE.match(left):
            to_at = find_word(right, "to")
            if to_at != -1:
                start = right[:to_at].strip()
                end = right[to_at + 2:].strip()
                step = None
                step_at = find_word(end, "step")
                if step_at != -1:
                    step = end[step_at + 4:].strip()
                    end = end[:step_at].strip()
                self.check_name(left, line)
                return self.block(Node("forrange", line, text, var=left, start=start,
                                       end=end, step=step))
        raise JadivSyntaxError("For is written 'For each X in List' or 'For I = 1 to 10'",
                               line=line, code="E220")

    def st_function(self, rest, line, text, inline):
        name, params_text = first_word(rest)
        if not name:
            raise JadivSyntaxError("Function needs a name", line=line, code="E221",
                                   hint="Example: Function Greet Name")
        self.check_name(name, line)
        params = [p for p in re.split(r"[,\s]+", params_text.strip("() ")) if p]
        for param in params:
            if not NAME_RE.match(param):
                raise JadivSyntaxError(f"'{param}' is not a valid name", line=line, code="E221")
        return self.block(Node("function", line, text, name=name, params=params))

    def st_return(self, rest, line, text, inline):
        return Node("return", line, text, raw=rest)

    def st_call(self, rest, line, text, inline):
        name, args = first_word(rest)
        if not name:
            raise JadivSyntaxError("Call needs a function name", line=line, code="E222",
                                   hint="Example: Call Greet \"Jan\"")
        return Node("call", line, text, name=name, raw=args.strip())

    def st_on(self, rest, line, text, inline):
        keep = False
        head = rest
        m = re.search(r'(?i)"\s+else\s+(\w+)\s*$', head)
        if m:
            if m.group(1).lower() != "loop":
                raise JadivSyntaxError("After an On statement only 'Else loop' is allowed",
                                       line=line, code="E223")
            keep = True
            head = head[:m.start() + 1]
        head, statement = split_quoted_statement(head)
        head = re.sub(r"(?i)\s+do\s*$", "", head).strip()
        if not head:
            raise JadivSyntaxError("On needs an event", line=line, code="E223",
                                   hint="Example: On ButtonPress \"Write = pressed\"")
        node = Node("on", line, text, raw=head, keep=keep, body=[])
        if statement is not None:
            node.body = [self.parse_line(statement, line, inline=True)]
            return node
        if inline:
            raise JadivSyntaxError("A one-line On needs a statement in quotes", line=line,
                                   code="E223")
        return self.block(node)

    def st_start(self, rest, line, text, inline):
        if not rest:
            raise JadivSyntaxError("Start needs a name", line=line, code="E224",
                                   hint="Example: Start Window")
        return Node("start", line, text, raw=rest)

    def st_stop(self, rest, line, text, inline):
        if not rest:
            return Node("exit", line, text)
        return Node("stopobj", line, text, raw=rest)

    def st_exit(self, rest, line, text, inline):
        return Node("exit", line, text)

    def st_break(self, rest, line, text, inline):
        return Node("break", line, text)

    def st_continue(self, rest, line, text, inline):
        return Node("continue", line, text)

    def st_strict(self, rest, line, text, inline):
        low = rest.lower()
        if low not in ("on", "off", ""):
            raise JadivSyntaxError("Strict is written 'Strict ON' or 'Strict OFF'",
                                   line=line, code="E225")
        return Node("strict", line, text, on=low != "off")

    def st_add(self, rest, line, text, inline):
        at = find_word(rest, "to", last=True)
        if at == -1 or not NAME_RE.match(rest[at + 2:].strip()):
            raise JadivSyntaxError("Add is written 'Add value to List'", line=line, code="E226")
        return Node("add", line, text, raw=rest[:at].strip(), name=rest[at + 2:].strip())

    def st_remove(self, rest, line, text, inline):
        at = find_word(rest, "from", last=True)
        if at == -1 or not NAME_RE.match(rest[at + 4:].strip()):
            raise JadivSyntaxError("Remove is written 'Remove value from List'",
                                   line=line, code="E227")
        return Node("remove", line, text, raw=rest[:at].strip(), name=rest[at + 4:].strip())

    def check_name(self, name, line):
        if name.lower() in KEYWORDS or name.lower() in ("on", "off", "true", "false",
                                                        "nothing", "and",
                                                        "or", "not", "is", "then"):
            raise JadivSyntaxError(f"'{name}' is a JadivCode word and cannot be used as a name",
                                   line=line, code="E230", hint="Pick a different name.")


def parse_program(source):
    """Parse source text. Returns (nodes, errors)."""
    parser = ProgramParser(source)
    nodes = parser.parse()
    return nodes, parser.errors
