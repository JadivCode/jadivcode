"""Command line interface: ``jadivcode run program.jdv`` and friends."""

import argparse
import json
import os
import shutil
import subprocess
import sys

from . import FILE_EXTENSION, __version__
from .checker import apply_fixes, check_source
from .errors import ExitSignal, JadivError
from .interpreter import Runtime, to_text
from .libs import LIBRARIES

TEMPLATE = """// {name} - a JadivCode program
// Run it with: jadivcode run {file}

Write = Hello from {name}!
Ask Name = What is your name?
Write = Nice to meet you, {{Name}}.
"""

COLORS = {"error": "\033[31m", "warning": "\033[33m", "info": "\033[36m"}
RESET = "\033[0m"


def read_source(path):
    if not os.path.exists(path) and os.path.exists(path + FILE_EXTENSION):
        path += FILE_EXTENSION
    try:
        with open(path, encoding="utf-8") as handle:
            return path, handle.read()
    except FileNotFoundError:
        fail(f"The file '{path}' does not exist")
    except IsADirectoryError:
        fail(f"'{path}' is a folder, not a program")
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"Cannot read '{path}': {exc}")


def fail(message, code=2):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def show(diags, path, color):
    for diag in diags:
        text = diag.format(path)
        if color:
            head, _, rest = text.partition("\n")
            text = COLORS[diag.severity] + head + RESET + ("\n" + rest if rest else "")
        if diag.fix:
            text += f"\n  fix available: {diag.fix['title']}"
        print(text, file=sys.stderr)


def cmd_run(args):
    path, source = read_source(args.file)
    if args.sim:
        os.environ["JADIVCODE_GPIO"] = "sim"
        os.environ["JADIVCODE_GUI"] = "headless"
    diags = check_source(source)
    errors = [d for d in diags if d.severity == "error"]
    color = sys.stderr.isatty()
    if errors and not args.force:
        show(errors, path, color)
        print(f"\n{len(errors)} error(s). The program was not started. "
              f"Run 'jadivcode check {path}' for details"
              + (" or add --fix to repair them." if any(d.fix for d in errors) else "."),
              file=sys.stderr)
        return 1
    if not args.quiet:
        show([d for d in diags if d.severity == "warning"], path, color)
    runtime = Runtime(filename=path, strict=args.strict, args=args.args)
    try:
        return runtime.run_source(source)
    except JadivError as exc:
        print("error: " + exc.format(path), file=sys.stderr)
        return 1


def cmd_check(args):
    path, source = read_source(args.file)
    diags = check_source(source)
    if args.fix:
        fixed, count = apply_fixes(source, diags)
        if count:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(fixed)
            print(f"Applied {count} fix(es) to {path}.")
            source = fixed
            diags = check_source(source)
    if args.json:
        print(json.dumps([d.as_dict() for d in diags], indent=2))
    else:
        show(diags, path, sys.stderr.isatty())
        errors = sum(d.severity == "error" for d in diags)
        warnings = sum(d.severity == "warning" for d in diags)
        if not diags:
            print(f"{path}: no problems found.")
        else:
            print(f"{path}: {errors} error(s), {warnings} warning(s), "
                  f"{len(diags) - errors - warnings} tip(s).")
    return 1 if any(d.severity == "error" for d in diags) else 0


def cmd_new(args):
    name = args.name
    path = name if name.endswith(FILE_EXTENSION) else name + FILE_EXTENSION
    if os.path.exists(path):
        fail(f"'{path}' already exists")
    title = os.path.splitext(os.path.basename(path))[0]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(TEMPLATE.format(name=title, file=path))
    print(f"Created {path}. Run it with: jadivcode run {path}")
    return 0


def cmd_libs(args):
    wanted = args.name.lower() if args.name else None
    if wanted and wanted not in LIBRARIES:
        fail(f"There is no library called '{args.name}'")
    for cls in LIBRARIES.values():
        if wanted and cls.name.lower() != wanted:
            continue
        print(f"{cls.name} - {cls.summary}")
        if wanted or args.all:
            width = max(len(c[0]) for c in cls.commands)
            for command, description in cls.commands:
                print(f"  {command.ljust(width)}  {description}")
            print()
    if not wanted and not args.all:
        print("\nShow the commands of one library with: jadivcode libs Math")
    return 0


def cmd_repl(args):
    print(f"JadivCode {__version__} - type statements, 'Exit' to quit.")
    runtime = Runtime(filename=None)
    buffer = []
    depth = 0
    openers = ("if", "repeat", "while", "forever", "for", "function", "on")
    while True:
        try:
            line = input("... " if buffer else "jdv> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        word = line.strip().split(" ")[0].lower() if line.strip() else ""
        one_line = (word == "if" and " then " in line.lower() and
                    line.lower().split(" then ", 1)[1].strip()) or \
                   (word == "on" and ("=" in line or line.rstrip().lower().endswith("loop")))
        if word in openers and not one_line:
            depth += 1
        elif word == "end" and depth:
            depth -= 1
        buffer.append(line)
        if depth:
            continue
        source = "\n".join(buffer)
        buffer = []
        from .parser import parse_program
        nodes, errors = parse_program(source)
        if errors:
            print("error: " + errors[0].format(), file=sys.stderr)
            continue
        try:
            runtime.exec_block(nodes)
            runtime.pump(0)
        except ExitSignal:
            break
        except JadivError as exc:
            print("error: " + exc.format(), file=sys.stderr)
        except KeyboardInterrupt:
            print()
    runtime.shutdown()
    return 0


def cmd_explain(args):
    from .ai import explain
    path, source = read_source(args.file)
    try:
        print(explain(source, question=" ".join(args.question) or None))
    except JadivError as exc:
        print("error: " + exc.format(), file=sys.stderr)
        return 1
    return 0


def cmd_ide(args):
    program = shutil.which("jadivcode-ide")
    if program is None:
        fail("The JadivCode IDE is not installed.\n"
             "Install it from https://github.com/JadivCode/jadivcode-ide")
    return subprocess.call([program] + args.files)


def cmd_eval(args):
    runtime = Runtime()
    try:
        print(to_text(runtime.eval_value(" ".join(args.expression))))
    except JadivError as exc:
        print("error: " + exc.format(), file=sys.stderr)
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jadivcode", description="JadivCode - a super simple programming language.")
    parser.add_argument("--version", action="version", version=f"JadivCode {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    run = sub.add_parser("run", help="run a program")
    run.add_argument("file")
    run.add_argument("args", nargs="*", help="values for {System Args}")
    run.add_argument("--strict", action="store_true", help="stop at the first error")
    run.add_argument("--sim", action="store_true",
                     help="simulate GPIO pins and GUI windows")
    run.add_argument("--force", action="store_true", help="run even when the checker finds errors")
    run.add_argument("--quiet", action="store_true", help="do not show checker warnings")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="find mistakes and unsafe code without running")
    check.add_argument("file")
    check.add_argument("--fix", action="store_true", help="apply the available fixes")
    check.add_argument("--json", action="store_true", help="machine readable output")
    check.set_defaults(func=cmd_check)

    new = sub.add_parser("new", help="create a new program from a template")
    new.add_argument("name")
    new.set_defaults(func=cmd_new)

    libs = sub.add_parser("libs", help="list the libraries and their commands")
    libs.add_argument("name", nargs="?")
    libs.add_argument("--all", action="store_true", help="show the commands of every library")
    libs.set_defaults(func=cmd_libs)

    repl = sub.add_parser("repl", help="try statements interactively")
    repl.set_defaults(func=cmd_repl)

    explain = sub.add_parser("explain", help="ask the optional AI helper about a program")
    explain.add_argument("file")
    explain.add_argument("question", nargs="*")
    explain.set_defaults(func=cmd_explain)

    ide = sub.add_parser("ide", help="open the JadivCode IDE")
    ide.add_argument("files", nargs="*")
    ide.set_defaults(func=cmd_ide)

    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 'jadivcode program.jdv' is a shortcut for 'jadivcode run program.jdv'.
    if argv and argv[0].endswith(FILE_EXTENSION):
        argv.insert(0, "run")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
