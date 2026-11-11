"""File library: read and write text files."""

import os

from ..errors import JadivRuntimeError
from . import Library


class FileLibrary(Library):
    name = "File"
    summary = "Read and write text files."
    commands = (
        ('{File Read "notes.txt"}', "The whole file as text"),
        ('{File Lines "notes.txt"}', "The file as a list of lines"),
        ('{File Exists "notes.txt"}', "ON when the file exists"),
        ('File Write "notes.txt" = text', "Replace the file with text"),
        ('File Append "log.txt" = text', "Add a line to the end of the file"),
        ('File Delete "notes.txt"', "Delete the file"),
    )

    def path(self, args):
        value = args.take_value()
        if not isinstance(value, str) or not value.strip():
            raise JadivRuntimeError("File needs a file name in quotes", code="E410",
                                    hint='Example: {File Read "notes.txt"}')
        return os.path.expanduser(value)

    def guard(self, action, path):
        try:
            return action()
        except FileNotFoundError:
            raise JadivRuntimeError(f"The file '{path}' does not exist", code="E411")
        except IsADirectoryError:
            raise JadivRuntimeError(f"'{path}' is a folder, not a file", code="E412")
        except PermissionError:
            raise JadivRuntimeError(f"No permission to use '{path}'", code="E413")
        except (OSError, UnicodeDecodeError) as exc:
            raise JadivRuntimeError(f"Cannot use '{path}': {exc}", code="E414")

    def read(self, path):
        def action():
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        return self.guard(action, path)

    def query(self, args):
        verb = args.take_word()
        if verb == "read":
            return self.read(self.path(args))
        if verb == "lines":
            return self.read(self.path(args)).splitlines()
        if verb == "exists":
            return os.path.exists(self.path(args))
        self.unknown(args)

    def statement(self, args, value):
        from ..interpreter import to_text
        verb = args.take_word()
        if verb in ("write", "append"):
            path = self.path(args)
            text = to_text(value) if value is not None else ""

            def action():
                with open(path, "w" if verb == "write" else "a", encoding="utf-8") as handle:
                    handle.write(text)
                    if not text.endswith("\n"):
                        handle.write("\n")
            self.guard(action, path)
            return
        if verb == "delete":
            path = self.path(args)
            self.guard(lambda: os.remove(path), path)
            return
        self.unknown(args)
