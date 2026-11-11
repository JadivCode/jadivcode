"""System library: run programs and read the environment."""

import os
import platform
import shlex
import subprocess

from ..errors import JadivRuntimeError
from . import Library


class SystemLibrary(Library):
    name = "System"
    summary = "Run other programs and read information about the computer."
    commands = (
        ('{System Run "ls -l"}', "Run a program and get its output (no shell is used)"),
        ('System Run "sync"', "Run a program and show its output"),
        ('{System Env "HOME"}', "Value of an environment variable"),
        ("{System Args}", "Values given after the file name on the command line"),
        ("{System Platform}", "Computer type, e.g. Linux aarch64"),
        ("{System User}", "Name of the current user"),
    )

    def run(self, args):
        from ..interpreter import to_text
        values = args.values()
        if not values:
            raise JadivRuntimeError("System Run needs a command in quotes", code="E420",
                                    hint='Example: {System Run "uname -r"}')
        try:
            # The first value is split into words; further values are passed
            # to the program untouched, so they can never be read as extra
            # commands or options by a shell.
            command = shlex.split(to_text(values[0])) + [to_text(v) for v in values[1:]]
        except ValueError:
            raise JadivRuntimeError("The command has a quote that is never closed",
                                    code="E421")
        if not command:
            raise JadivRuntimeError("System Run needs a command", code="E420")
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=300,
                                  check=False)
        except FileNotFoundError:
            raise JadivRuntimeError(f"The program '{command[0]}' was not found", code="E422")
        except PermissionError:
            raise JadivRuntimeError(f"No permission to run '{command[0]}'", code="E423")
        except subprocess.TimeoutExpired:
            raise JadivRuntimeError(f"'{command[0]}' did not finish within 5 minutes",
                                    code="E424")
        output = (done.stdout or "").rstrip("\n")
        if done.returncode != 0:
            detail = (done.stderr or "").strip().splitlines()
            raise JadivRuntimeError(
                f"'{command[0]}' failed with code {done.returncode}"
                + (f": {detail[-1]}" if detail else ""), code="E425")
        return output

    def query(self, args):
        verb = args.take_word()
        if verb == "run":
            return self.run(args)
        if verb == "env":
            return os.environ.get(str(args.take_value("")), "")
        if verb == "args":
            return list(self.rt.args)
        if verb == "platform":
            return f"{platform.system()} {platform.machine()}"
        if verb == "user":
            return os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        self.unknown(args)

    def statement(self, args, value):
        verb = args.take_word()
        if verb == "run":
            output = self.run(args)
            if output:
                self.rt.out(output)
            return
        self.unknown(args)
