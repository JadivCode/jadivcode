"""Optional AI helper: explains problems in a program and proposes a fix.

This feature is off unless the user asks for it (``jadivcode explain`` or
the AI button in the IDE).  It needs the ``anthropic`` Python package and
the user's own credentials (``ANTHROPIC_API_KEY`` or ``ant auth login``).
The program text is sent to the Claude API; nothing is sent otherwise.
"""

import os

from .checker import check_source
from .errors import JadivError

DEFAULT_MODEL = "claude-opus-5-5"
MAX_SOURCE_CHARS = 200_000

SYSTEM_PROMPT = """You help people write programs in JadivCode, a small line-based \
programming language for Linux. Many users are beginners, so explain in plain words \
and keep answers short.

Language summary:
- One statement per line. Names and keywords are not case sensitive. Comments start with //.
- Import GPIO | Math | GUI | WebUI | Web | File | System | Time
- Define Name = value        (create a variable or a library object, e.g. Define Lamp = GPIO #17)
- Define Name = Lib Verb {} on Target #1   (a shortcut; {} marks where the arguments go)
- Set Name = value, Write = text with {expressions}, Ask Name = question, Wait 5s / 200ms / 2m
- If cond then stmt else stmt   (one line)  or  If / Else If / Else / End blocks
- Repeat N times, While cond, Forever, For each X in List, For I = 1 to 10 ... End, Break, Continue
- Function Name A, B ... Return value ... End; Call Name 1, 2; or {Name(1, 2)} in expressions
- On Event "statement"  or  On Event ... End; inside, {Event} holds the event value
- Lists: Define L = List 1, 2, 3; Add 4 to L; Remove 2 from L; {L #1}; {Length(L)}
- A value after = is calculated when it is a valid expression of known names, otherwise it is text.
- Success is "Full", "Partial" or "None"; Error holds the last error message. Strict ON stops on errors.

You receive the program and the findings of the built-in checker. Reply with:
1. What is wrong, most important first, each with its line number and why it matters.
2. The corrected program in one code block, changing as little as possible.
Mention security problems (secrets in the file, unencrypted http, services open to the \
network, outside values in commands or file names) when you see them. If the program is \
fine, say so in one sentence and do not rewrite it."""


class AIUnavailable(JadivError):
    """The AI helper cannot be used (missing package or credentials)."""


def explain(source, question=None, model=None):
    """Ask Claude to explain and fix ``source``. Returns the reply text."""
    try:
        import anthropic
    except ImportError:
        raise AIUnavailable(
            "The AI helper needs the 'anthropic' package",
            hint="Install it with: python3 -m pip install --user anthropic", code="A001")
    if len(source) > MAX_SOURCE_CHARS:
        raise AIUnavailable("The program is too large to send to the AI helper", code="A002",
                            hint="Ask about a smaller part of it.")
    findings = "\n".join(d.format() for d in check_source(source)) or "No findings."
    numbered = "\n".join(f"{i:>4}  {line}" for i, line in enumerate(source.splitlines(), 1))
    content = (f"<program>\n{numbered}\n</program>\n\n"
               f"<checker_findings>\n{findings}\n</checker_findings>\n\n"
               + (f"My question: {question}" if question else
                  "Explain the problems and show the fixed program."))
    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=model or os.environ.get("JADIVCODE_AI_MODEL") or DEFAULT_MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise AIUnavailable(
            "The AI helper has no valid credentials", code="A003",
            hint="Set ANTHROPIC_API_KEY in your environment, or run: ant auth login")
    except anthropic.RateLimitError:
        raise AIUnavailable("The AI service is busy, try again in a moment", code="A004")
    except anthropic.APIStatusError as exc:
        raise AIUnavailable(f"The AI service answered with error {exc.status_code}",
                            code="A005")
    except anthropic.APIConnectionError:
        raise AIUnavailable("Cannot reach the AI service (check the internet connection)",
                            code="A006")
    except Exception as exc:  # noqa: BLE001 - e.g. no credentials configured at all
        raise AIUnavailable(f"The AI helper could not start: {exc}", code="A003",
                            hint="Set ANTHROPIC_API_KEY in your environment, or run: "
                                 "ant auth login")
    if response.stop_reason == "refusal":
        raise AIUnavailable("The AI service declined to answer this request", code="A007")
    return "\n".join(block.text for block in response.content if block.type == "text").strip()
