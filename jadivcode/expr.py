"""Expression tokenizer and parser for JadivCode.

Expressions appear inside ``{ }`` in text, in conditions and on the right
side of ``=``.  The grammar is deliberately small::

    or  ->  and  ->  not  ->  comparison  ->  + -  ->  * / %  ->  unary  ->  ^
        ->  postfix (#index)  ->  primary

Parsing produces plain tuples; evaluation lives in ``interpreter.py``.
"""

from .errors import JadivSyntaxError

TRUE_WORDS = {"on", "true"}
FALSE_WORDS = {"off", "false"}
NOTHING_WORDS = {"nothing"}
WORD_OPERATORS = {"and", "or", "not", "is"}

TWO_CHAR_OPS = ("==", "!=", "<>", "<=", ">=")
ONE_CHAR_OPS = "+-*/%^(),=<>#"


class Tok:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind  # NUM, STR, ID, OP
        self.value = value
        self.pos = pos

    def __repr__(self):
        return f"Tok({self.kind}, {self.value!r})"

    def is_op(self, value):
        return self.kind == "OP" and self.value == value

    def is_word(self, word):
        return self.kind == "ID" and self.value.lower() == word


def _is_ident_start(ch):
    return ch.isalpha() or ch == "_"


def _is_ident_char(ch):
    return ch.isalnum() or ch == "_"


def scan_string(text, start):
    """Return the index just past the closing quote of a string.

    ``text[start]`` must be a double quote.  Quotes inside ``{ }`` belong to
    the embedded expression, so ``"result {Math "5x5"}"`` is one string.
    """
    i = start + 1
    depth = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == '"':
            if depth == 0:
                return i + 1
            # A quote inside braces: skip the inner string.
            j = text.find('"', i + 1)
            if j == -1:
                break
            i = j
        i += 1
    raise JadivSyntaxError(
        "Text is missing its closing quote (\")",
        hint='Every " needs a matching " on the same line.',
        code="E101",
    )


def unescape(raw):
    return raw.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")


def tokenize(text):
    """Split expression text into tokens. Raises JadivSyntaxError."""
    toks = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            end = scan_string(text, i)
            toks.append(Tok("STR", unescape(text[i + 1:end - 1]), i))
            i = end
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot
                                                    and j + 1 < n and text[j + 1].isdigit())):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            if j < n and _is_ident_start(text[j]):
                raise JadivSyntaxError(
                    f"'{text[i:j + 1]}...' is not a number or a name", code="E102")
            raw = text[i:j]
            toks.append(Tok("NUM", float(raw) if seen_dot else int(raw), i))
            i = j
            continue
        if _is_ident_start(ch):
            j = i
            while j < n and _is_ident_char(text[j]):
                j += 1
            toks.append(Tok("ID", text[i:j], i))
            i = j
            continue
        two = text[i:i + 2]
        if two in TWO_CHAR_OPS:
            toks.append(Tok("OP", two, i))
            i += 2
            continue
        if ch in ONE_CHAR_OPS:
            toks.append(Tok("OP", ch, i))
            i += 1
            continue
        raise JadivSyntaxError(f"Unexpected character '{ch}'", code="E103")
    return toks


class Parser:
    """Recursive-descent parser over a token list.

    ``is_lib(name)`` and ``is_macro(name)`` tell the parser which names start
    a library call; such a call takes every remaining token up to the end of
    the expression or an unmatched ``)``.
    """

    def __init__(self, toks, is_lib=lambda name: False, is_macro=lambda name: False):
        self.toks = toks
        self.i = 0
        self.is_lib = is_lib
        self.is_macro = is_macro

    # -- helpers ---------------------------------------------------------
    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def at_op(self, *values):
        tok = self.peek()
        return tok is not None and tok.kind == "OP" and tok.value in values

    def at_word(self, word):
        tok = self.peek()
        return tok is not None and tok.is_word(word)

    # -- grammar ---------------------------------------------------------
    def parse(self):
        if not self.toks:
            raise JadivSyntaxError("Expected a value but found nothing", code="E110")
        node = self.parse_or()
        if self.peek() is not None:
            tok = self.peek()
            raise JadivSyntaxError(f"Unexpected '{tok.value}'", code="E111")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.at_word("or"):
            self.next()
            node = ("bin", "or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.at_word("and"):
            self.next()
            node = ("bin", "and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.at_word("not"):
            self.next()
            return ("un", "not", self.parse_not())
        return self.parse_compare()

    def parse_compare(self):
        node = self.parse_add()
        while True:
            if self.at_op("=", "==", "!=", "<>", "<", ">", "<=", ">="):
                op = self.next().value
                if op == "<>":
                    op = "!="
                node = ("bin", op, node, self.parse_add())
            elif self.at_word("is"):
                self.next()
                op = "="
                if self.at_word("not"):
                    self.next()
                    op = "!="
                node = ("bin", op, node, self.parse_add())
            else:
                return node

    def parse_add(self):
        node = self.parse_mul()
        while self.at_op("+", "-"):
            op = self.next().value
            node = ("bin", op, node, self.parse_mul())
        return node

    def parse_mul(self):
        node = self.parse_unary()
        while self.at_op("*", "/", "%"):
            op = self.next().value
            node = ("bin", op, node, self.parse_unary())
        return node

    def parse_unary(self):
        if self.at_op("-"):
            self.next()
            return ("un", "-", self.parse_unary())
        if self.at_op("+"):
            self.next()
            return self.parse_unary()
        return self.parse_power()

    def parse_power(self):
        node = self.parse_postfix()
        if self.at_op("^"):
            self.next()
            return ("bin", "^", node, self.parse_unary())
        return node

    def parse_postfix(self):
        node = self.parse_primary()
        while self.at_op("#"):
            self.next()
            node = ("index", node, self.parse_primary())
        return node

    def parse_primary(self):
        tok = self.next()
        if tok is None:
            raise JadivSyntaxError("The expression ends too early", code="E112")
        if tok.kind == "NUM":
            return ("num", tok.value)
        if tok.kind == "STR":
            return ("str", tok.value)
        if tok.is_op("("):
            node = self.parse_or()
            if not self.at_op(")"):
                raise JadivSyntaxError("Missing ')'", code="E113")
            self.next()
            return node
        if tok.kind == "ID":
            low = tok.value.lower()
            if low in TRUE_WORDS:
                return ("const", True)
            if low in FALSE_WORDS:
                return ("const", False)
            if low in NOTHING_WORDS:
                return ("const", None)
            if low in WORD_OPERATORS:
                raise JadivSyntaxError(f"'{tok.value}' cannot be used here", code="E114")
            if self.at_op("("):
                self.next()
                args = []
                if not self.at_op(")"):
                    args.append(self.parse_or())
                    while self.at_op(","):
                        self.next()
                        args.append(self.parse_or())
                if not self.at_op(")"):
                    raise JadivSyntaxError(
                        f"Missing ')' after the values for {tok.value}", code="E113")
                self.next()
                return ("call", tok.value, args)
            if self.is_macro(low) or self.is_lib(low):
                kind = "macro" if self.is_macro(low) else "lib"
                return (kind, tok.value, self.take_rest())
            return ("var", tok.value)
        raise JadivSyntaxError(f"Unexpected '{tok.value}'", code="E111")

    def take_rest(self):
        """Take tokens up to the end or an unmatched ')'."""
        depth = 0
        out = []
        while self.peek() is not None:
            tok = self.peek()
            if tok.is_op("("):
                depth += 1
            elif tok.is_op(")"):
                if depth == 0:
                    break
                depth -= 1
            out.append(self.next())
        return out


def names_in(node):
    """Yield (kind, name) for every variable, call, lib and macro in a tree."""
    kind = node[0]
    if kind == "var":
        yield ("var", node[1])
    elif kind == "call":
        yield ("call", node[1])
        for arg in node[2]:
            yield from names_in(arg)
    elif kind in ("lib", "macro"):
        yield (kind, node[1])
    elif kind == "index":
        yield from names_in(node[1])
        yield from names_in(node[2])
    elif kind == "un":
        yield from names_in(node[2])
    elif kind == "bin":
        yield from names_in(node[2])
        yield from names_in(node[3])


def split_template(text):
    """Split text into parts: ('text', s) and ('expr', source).

    ``{{`` and ``}}`` give literal braces; an empty ``{}`` stays literal.
    """
    parts = []
    buf = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{" and text[i:i + 2] == "{{":
            buf.append("{")
            i += 2
            continue
        if ch == "}" and text[i:i + 2] == "}}":
            buf.append("}")
            i += 2
            continue
        if ch == "{":
            depth = 1
            j = i + 1
            in_str = False
            while j < n and depth:
                c = text[j]
                if c == '"':
                    in_str = not in_str
                elif not in_str:
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                j += 1
            if depth:
                buf.append(text[i:])
                break
            inner = text[i + 1:j - 1]
            if inner.strip() == "":
                buf.append(text[i:j])
            else:
                if buf:
                    parts.append(("text", "".join(buf)))
                    buf = []
                parts.append(("expr", inner))
            i = j
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append(("text", "".join(buf)))
    return parts


def find_top_level(text, needle, start=0):
    """Find ``needle`` outside quotes and braces. Returns -1 when absent."""
    i = start
    n = len(text)
    depth = 0
    while i < n:
        ch = text[i]
        if ch == '"':
            try:
                i = scan_string(text, i)
            except JadivSyntaxError:
                return -1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(needle, i):
            return i
        i += 1
    return -1


def split_assignment(text):
    """Split ``left = right`` at the first top-level single '='.

    Returns (left, right) or (text, None) when there is no '='.
    """
    i = 0
    while True:
        i = find_top_level(text, "=", i)
        if i == -1:
            return text.strip(), None
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if prev in "<>!=" or nxt == "=":
            i += 2 if nxt == "=" else 1
            continue
        return text[:i].strip(), text[i + 1:].strip()
