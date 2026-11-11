"""Web library: HTTP requests and incoming webhooks.

Typical use: call a Home Assistant webhook, read a JSON API, or let
another system call this program.
"""

import hmac
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .. import __version__
from ..errors import JadivRuntimeError
from . import Library

MAX_BODY = 1024 * 1024
MAX_REPLY = 16 * 1024 * 1024


def parse_pairs(text):
    """'state: on, level: 50' -> {'state': 'on', 'level': 50}."""
    from ..interpreter import to_number
    result = {}
    for part in text.split(","):
        if not part.strip():
            continue
        if ":" not in part:
            raise JadivRuntimeError(
                f"'{part.strip()}' is not written as name: value", code="E470",
                hint='Example: Web Json "https://..." = state: on, level: 50')
        key, raw = part.split(":", 1)
        raw = raw.strip()
        low = raw.lower()
        if low in ("on", "true", "yes"):
            value = True
        elif low in ("off", "false", "no"):
            value = False
        else:
            number = to_number(raw)
            value = number if number is not None and raw else raw
        result[key.strip()] = value
    return result


class HookServer:
    """A small HTTP server that receives webhooks."""

    def __init__(self, lib, port):
        self.lib = lib
        self.port = port
        self.props = {"host": "127.0.0.1", "token": "", "reply": "OK"}
        self.hooks = {}
        self.last = {}
        self.server = None

    def __repr__(self):
        return f"Web Server #{self.port}"

    def jc_get(self):
        return self.server is not None

    def jc_set_prop(self, name, value):
        from ..interpreter import to_text
        low = name.lower()
        if low not in self.props:
            raise JadivRuntimeError(f"A Web Server has no property '{name}'", code="E471",
                                    hint="Properties: Host, Token, Reply.")
        self.props[low] = to_text(value)

    def listen(self, path, callback):
        path = "/" + str(path or "").strip().lstrip("/")
        self.hooks.setdefault(path.lower(), []).append(callback)
        if self.server is None:
            self.jc_start()

    def deliver(self, path, event):
        """Runs on the program thread."""
        self.last[path] = event
        for callback in self.hooks.get(path, []):
            callback(event)

    def jc_start(self):
        if self.server is not None:
            return
        hook_server = self
        token = self.props["token"]
        host = self.props["host"] or "127.0.0.1"

        class Handler(BaseHTTPRequestHandler):
            server_version = "JadivCode"

            def log_message(self, *args):
                pass

            def reply(self, code, body):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_hook(self):
                url = urlparse(self.path)
                path = url.path.rstrip("/").lower() or "/"
                query = parse_qs(url.query)
                if token:
                    given = self.headers.get("Authorization", "")
                    given = given[7:] if given.lower().startswith("bearer ") else ""
                    given = given or query.get("token", [""])[0]
                    if not hmac.compare_digest(given.encode(), token.encode()):
                        self.reply(401, "Unauthorized")
                        return
                if path not in hook_server.hooks:
                    self.reply(404, "Not found")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_BODY:
                    self.reply(413, "Too large")
                    return
                body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                event = body
                if body.strip():
                    try:
                        event = json.loads(body)
                    except ValueError:
                        event = body
                elif query:
                    event = {k: v[0] for k, v in query.items() if k != "token"}
                hook_server.lib.rt.post(hook_server.deliver, path, event)
                self.reply(200, hook_server.props["reply"])

            do_GET = do_POST = do_PUT = handle_hook

        try:
            self.server = ThreadingHTTPServer((host, self.port), Handler)
        except OSError as exc:
            raise JadivRuntimeError(
                f"Cannot start the server on {host}:{self.port}: {exc.strerror or exc}",
                code="E472", hint="The port may be in use; pick another number.")
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.lib.rt.out(f"[Web] server is listening on http://{host}:{self.port}/")
        if host not in ("127.0.0.1", "localhost", "::1") and not token:
            self.lib.rt.err("warning: the Web Server is open to the network without a token "
                            "(add: Set Server Token = ...)")

    def jc_stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


class WebLibrary(Library):
    name = "Web"
    summary = "HTTP requests and webhooks (Home Assistant, REST APIs...)."
    commands = (
        ('{Web Get "https://example.com"}', "Download a page as text"),
        ('{Web Json "https://api.example.com/data"}', "Download JSON as a map/list"),
        ('Web Post "https://host/api/webhook/id" = text', "Send text"),
        ('Web Json "https://host/api/webhook/id" = state: on, level: 50', "Send JSON"),
        ('Web Header "Authorization" = Bearer {Token}', "Add a header to every request"),
        ("{Web Status}", "Status code of the last request (200 = OK)"),
        ("{Web Reply}", "Text of the last reply"),
        ("Define Server = Web Server #8090", "A server for incoming webhooks"),
        ("Define Hook = Web Read Hook {} on Server #8090", "Shortcut for hooks"),
        ('On Hook "/lamp" "Write = {Event}"', "Run a statement when the hook is called"),
    )

    def __init__(self, runtime):
        super().__init__(runtime)
        self.headers = {"User-Agent": f"JadivCode/{__version__}"}
        self.timeout = 15
        self.status = 0
        self.reply = ""
        self.servers = {}

    # -- requests --------------------------------------------------------
    def url(self, args):
        value = args.take_value()
        if not isinstance(value, str) or not value.strip():
            raise JadivRuntimeError("Web needs an address in quotes", code="E473",
                                    hint='Example: {Web Get "https://example.com"}')
        value = value.strip()
        if urlparse(value).scheme.lower() not in ("http", "https"):
            raise JadivRuntimeError(
                f"'{value}' is not a web address", code="E474",
                hint="Addresses start with https:// or http://")
        return value

    def request(self, url, data=None, content_type=None, method=None):
        headers = dict(self.headers)
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.status = resp.status
                raw = resp.read(MAX_REPLY + 1)
        except urllib.error.HTTPError as exc:
            self.status = exc.code
            self.reply = exc.read(MAX_REPLY).decode("utf-8", "replace")
            raise JadivRuntimeError(f"{url} answered with error {exc.code} {exc.reason}",
                                    code="E475")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.status = 0
            self.reply = ""
            reason = getattr(exc, "reason", exc)
            raise JadivRuntimeError(f"Cannot reach {url}: {reason}", code="E476")
        if len(raw) > MAX_REPLY:
            raise JadivRuntimeError("The reply is larger than 16 MB", code="E477")
        self.reply = raw.decode("utf-8", "replace")
        return self.reply

    def send(self, url, value, as_json):
        from ..interpreter import deref, to_text
        value = deref(value)
        if as_json:
            if isinstance(value, str):
                payload = parse_pairs(value)
            elif value is None:
                payload = {}
            else:
                payload = value
            data = json.dumps(payload, default=to_text).encode("utf-8")
            return self.request(url, data, "application/json", "POST")
        text = "" if value is None else to_text(value)
        return self.request(url, text.encode("utf-8"), "text/plain; charset=utf-8", "POST")

    # -- servers ---------------------------------------------------------
    def server(self, args, create=False):
        obj = args.take_object(HookServer)
        if obj is not None:
            return obj
        args.take_word("server")
        port = args.take_ref()
        if port is None:
            if len(self.servers) == 1:
                return next(iter(self.servers.values()))
            raise JadivRuntimeError("Say which server to use", code="E478",
                                    hint="Example: on Server #8090")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise JadivRuntimeError(f"{port} is not a valid port", code="E479",
                                    hint="Use a port from 1024 to 65535.")
        server = self.servers.get(port)
        if server is None:
            if not create and self.servers:
                raise JadivRuntimeError(f"Web Server #{port} is not defined", code="E478")
            server = HookServer(self, port)
            self.servers[port] = server
        return server

    def target(self, args):
        if args.target is not None:
            return self.server(args.target)
        if len(self.servers) == 1:
            return next(iter(self.servers.values()))
        raise JadivRuntimeError("Say which server to use", code="E478",
                                hint="Example: on Server #8090")

    # -- interface -------------------------------------------------------
    def query(self, args):
        verb = args.take_word()
        if verb == "get":
            return self.request(self.url(args))
        if verb == "json":
            url = self.url(args)
            values = args.values()
            text = self.send(url, values[0], True) if values else self.request(url)
            try:
                return json.loads(text)
            except ValueError:
                raise JadivRuntimeError(f"{url} did not answer with JSON", code="E480")
        if verb == "post":
            url = self.url(args)
            values = args.values()
            return self.send(url, values[0] if values else None, False)
        if verb == "status":
            return self.status
        if verb == "reply":
            return self.reply
        if verb == "server":
            return self.server(args, create=True)
        if verb == "read":
            args.take_word("hook")
            path = "/" + str(args.take_value("")).strip().lstrip("/")
            return self.target(args).last.get(path.lower())
        self.unknown(args)

    def statement(self, args, value):
        from ..interpreter import to_number, to_text
        verb = args.take_word()
        if verb == "get":
            self.request(self.url(args))
        elif verb in ("post", "json"):
            self.send(self.url(args), value, verb == "json")
        elif verb == "header":
            name = str(args.take_value("")).strip()
            if not name or any(ch in name for ch in "\r\n:"):
                raise JadivRuntimeError("Web Header needs a header name in quotes",
                                        code="E481")
            text = to_text(value) if value is not None else ""
            if "\r" in text or "\n" in text:
                raise JadivRuntimeError("A header cannot contain line breaks", code="E481")
            self.headers[name] = text
        elif verb == "timeout":
            number = to_number(value)
            if number is None or number <= 0:
                raise JadivRuntimeError("Web Timeout needs a number of seconds", code="E482")
            self.timeout = number
        else:
            self.unknown(args)

    def subscribe(self, args, callback):
        args.take_word("read")
        if args.take_word("hook") is None:
            raise JadivRuntimeError("The Web event is written: Web Read Hook \"/path\"",
                                    code="E483")
        self.target(args).listen(args.take_value(""), callback)

    def alive(self):
        return any(server.server is not None for server in self.servers.values())

    def close(self):
        for server in self.servers.values():
            server.jc_stop()
