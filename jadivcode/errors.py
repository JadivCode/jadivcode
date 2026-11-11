"""Error types used by the JadivCode interpreter."""


class JadivError(Exception):
    """Base class for every error reported to the user."""

    def __init__(self, message, line=None, hint=None, code="E000"):
        super().__init__(message)
        self.message = message
        self.line = line
        self.hint = hint
        self.code = code

    def format(self, filename=None):
        where = ""
        if filename:
            where += f"{filename}:"
        if self.line:
            where += f"{self.line}:"
        text = f"{where} {self.message}".strip()
        if self.hint:
            text += f"\n  hint: {self.hint}"
        return text


class JadivSyntaxError(JadivError):
    """The program text is not valid JadivCode."""


class JadivRuntimeError(JadivError):
    """Something failed while the program was running."""


class ControlSignal(Exception):
    """Internal control flow (never shown to the user)."""


class BreakSignal(ControlSignal):
    pass


class ContinueSignal(ControlSignal):
    pass


class ReturnSignal(ControlSignal):
    def __init__(self, value=None):
        super().__init__()
        self.value = value


class ExitSignal(ControlSignal):
    def __init__(self, code=0):
        super().__init__()
        self.code = code
