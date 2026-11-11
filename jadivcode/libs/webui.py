"""WebUI library: the same items as GUI, shown as a web page.

``Start Page`` runs a small web server.  It listens on this computer only
(127.0.0.1) unless ``Host`` is changed; when you open it to the network,
set a ``Password`` as well.
"""

import base64
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..errors import JadivRuntimeError
from . import Library
from .widgets import Surface, SurfaceLibrary

MAX_BODY = 64 * 1024
POLL_SECONDS = 20

PAGE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JadivCode</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#1c2230;--line:#d5d9e2;--accent:#2f6fed;--accent-ink:#fff}
@media (prefers-color-scheme: dark){:root{--bg:#12151c;--card:#1b202b;--ink:#e8ebf2;
--line:#333b4d;--accent:#5b8dff;--accent-ink:#0c1020}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,sans-serif}
header{padding:16px 20px;font-size:20px;font-weight:600}
main{margin:0 16px 24px;padding:16px;background:var(--card);border:1px solid var(--line);
border-radius:12px;position:relative;min-height:120px}
.item{margin:0 0 12px}
.abs{position:absolute;margin:0}
button{font:inherit;padding:8px 16px;border:0;border-radius:8px;background:var(--accent);
color:var(--accent-ink);cursor:pointer}
button:disabled,input:disabled{opacity:.5;cursor:default}
input[type=text]{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
background:transparent;color:inherit;width:100%;max-width:320px}
input[type=range]{width:100%;max-width:320px}
label{display:flex;gap:8px;align-items:center}
img{max-width:100%}
#off{display:none;padding:8px 20px;color:#c0392b}
</style></head><body>
<header id="title">JadivCode</header><div id="off">Not connected - the program is not running.</div>
<main id="main"></main>
<script>
const token = "__TOKEN__";
let version = -1;
const nodes = {};
function send(id, value){
  fetch("event", {method:"POST", headers:{"Content-Type":"application/json",
    "X-JadivCode-Token": token}, body: JSON.stringify({id:id, value:value})});
}
function make(w){
  const box = document.createElement("div");
  let el;
  if (w.kind === "button"){ el = document.createElement("button");
    el.addEventListener("click", () => send(w.id, true)); box.appendChild(el); }
  else if (w.kind === "input"){ el = document.createElement("input"); el.type = "text";
    el.addEventListener("change", () => send(w.id, el.value)); box.appendChild(el); }
  else if (w.kind === "checkbox"){ const l = document.createElement("label");
    el = document.createElement("input"); el.type = "checkbox";
    el.addEventListener("change", () => send(w.id, el.checked));
    const s = document.createElement("span"); l.appendChild(el); l.appendChild(s);
    box.appendChild(l); box.caption = s; }
  else if (w.kind === "slider"){ el = document.createElement("input"); el.type = "range";
    el.addEventListener("change", () => send(w.id, Number(el.value))); box.appendChild(el); }
  else if (w.kind === "image"){ el = document.createElement("img"); box.appendChild(el); }
  else { el = document.createElement("div"); el.style.whiteSpace = "pre-wrap"; box.appendChild(el); }
  box.el = el; box.kind = w.kind;
  return box;
}
function update(box, w){
  const el = box.el, p = w.props || {}, pos = w.pos || {};
  if (w.kind === "button" || w.kind === "text") el.textContent = w.text;
  if (w.kind === "checkbox"){ box.caption.textContent = w.text; el.checked = !!w.value; }
  if (w.kind === "input" && document.activeElement !== el) el.value = w.value;
  if (w.kind === "slider"){ el.min = p.min ?? 0; el.max = p.max ?? 100;
    if (document.activeElement !== el) el.value = w.value; }
  if (w.kind === "image"){ const ok = /^(https?:|data:image\\/)/i.test(w.text);
    if (ok) el.src = w.text; el.alt = w.id; }
  el.style.color = p.color || ""; el.style.background = p.background || "";
  el.style.fontSize = p.size ? Number(p.size) + "px" : "";
  if ("disabled" in el) el.disabled = ("enabled" in p) &&
    [false, "OFF", "off", "false", "no", 0].includes(p.enabled);
  const abs = ("x" in pos) || ("y" in pos);
  box.className = abs ? "item abs" : "item";
  box.style.left = abs ? (pos.x || 0) + "px" : ""; box.style.top = abs ? (pos.y || 0) + "px" : "";
  box.style.width = pos.w ? pos.w + "px" : ""; box.style.height = pos.h ? pos.h + "px" : "";
}
function draw(state){
  version = state.version;
  const title = state.props.title || "JadivCode";
  document.title = title; document.getElementById("title").textContent = title;
  const main = document.getElementById("main");
  document.body.style.background = state.props.color || "";
  const seen = new Set(); let bottom = 0;
  for (const w of state.widgets){
    const key = w.id.toLowerCase(); seen.add(key);
    if (!nodes[key] || nodes[key].kind !== w.kind){
      if (nodes[key]) nodes[key].remove();
      nodes[key] = make(w); }
    main.appendChild(nodes[key]);
    update(nodes[key], w);
    if (w.pos && ("y" in w.pos)) bottom = Math.max(bottom, (w.pos.y || 0) + (w.pos.h || 40));
  }
  for (const key of Object.keys(nodes)) if (!seen.has(key)){ nodes[key].remove(); delete nodes[key]; }
  main.style.minHeight = Math.max(120, bottom + 16) + "px";
}
async function loop(){
  for(;;){
    try{
      const r = await fetch("state?v=" + version, {cache:"no-store"});
      if (!r.ok) throw new Error(r.status);
      draw(await r.json());
      document.getElementById("off").style.display = "none";
    }catch(e){
      document.getElementById("off").style.display = "block";
      await new Promise(res => setTimeout(res, 2000));
    }
  }
}
loop();
</script></body></html>
"""


class Page(Surface):
    label = "page"

    def __init__(self, lib, number):
        super().__init__(lib, number)
        self.props.update({"port": 8080, "host": "127.0.0.1", "password": ""})
        self.server = None
        self.thread = None
        self.token = secrets.token_urlsafe(24)
        self.changed = threading.Condition()
        self.cached = json.dumps(self.snapshot())
        self.cached_version = self.version

    # -- model -> browser -----------------------------------------------
    def public_snapshot(self):
        snap = self.snapshot()
        snap["props"] = {k: v for k, v in snap["props"].items()
                         if k not in ("password", "host", "port")}
        return snap

    def render(self, widget=None, removed=None):
        with self.changed:
            self.cached = json.dumps(self.public_snapshot(), default=str)
            self.cached_version = self.version
            self.changed.notify_all()

    def touch(self):
        super().touch()
        self.render()

    def wait_state(self, known):
        with self.changed:
            if known == self.cached_version and self.started:
                self.changed.wait(POLL_SECONDS)
            return self.cached

    # -- browser -> model -----------------------------------------------
    def receive(self, wid, value):
        """Runs on the program thread: validate, then fire the event."""
        widget = self.widgets.get(str(wid).lower())
        if widget is None:
            return
        if widget.kind == "input":
            value = str(value)[:10000]
        elif widget.kind == "checkbox":
            value = bool(value)
        elif widget.kind == "slider":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return
        else:
            value = None
        self.fire(widget.id, value)

    def authorized(self, header):
        password = str(self.props.get("password") or "")
        if not password:
            return True
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        given = decoded.split(":", 1)[-1]
        return hmac.compare_digest(given.encode(), password.encode())

    # -- lifecycle -------------------------------------------------------
    def show(self):
        if self.server is not None:
            return
        host = str(self.props.get("host") or "127.0.0.1")
        port = int(self.props.get("port") or 8080)
        if not 1 <= port <= 65535:
            self.started = False
            raise JadivRuntimeError(f"{port} is not a valid port", code="E460",
                                    hint="Use a port from 1024 to 65535.")
        page = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "JadivCode"

            def log_message(self, *args):
                pass

            def reply(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; img-src * data:; "
                                 "style-src 'unsafe-inline'; script-src 'unsafe-inline'")
                for name, value in (extra or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def allowed(self):
                if page.authorized(self.headers.get("Authorization")):
                    return True
                self.reply(401, "Password required",
                           extra={"WWW-Authenticate": 'Basic realm="JadivCode"'})
                return False

            def do_GET(self):
                if not self.allowed():
                    return
                url = urlparse(self.path)
                if url.path == "/":
                    self.reply(200, PAGE_HTML.replace("__TOKEN__", page.token),
                               "text/html; charset=utf-8")
                elif url.path == "/state":
                    try:
                        known = int(parse_qs(url.query).get("v", ["-1"])[0])
                    except ValueError:
                        known = -1
                    self.reply(200, page.wait_state(known), "application/json")
                else:
                    self.reply(404, "Not found")

            def do_POST(self):
                if not self.allowed():
                    return
                if urlparse(self.path).path != "/event":
                    self.reply(404, "Not found")
                    return
                given = self.headers.get("X-JadivCode-Token", "")
                if not hmac.compare_digest(given.encode(), page.token.encode()):
                    self.reply(403, "Forbidden")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_BODY:
                    self.reply(413, "Too large")
                    return
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                    wid, value = data["id"], data.get("value")
                except (ValueError, KeyError, TypeError):
                    self.reply(400, "Bad request")
                    return
                page.lib.rt.post(page.receive, wid, value)
                self.reply(200, "OK")

        try:
            self.server = ThreadingHTTPServer((host, port), Handler)
        except OSError as exc:
            self.started = False
            raise JadivRuntimeError(
                f"Cannot start the page on {host}:{port}: {exc.strerror or exc}", code="E461",
                hint="The port may be in use. Try another one: Set Port = 8081")
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        shown = "localhost" if host in ("127.0.0.1", "localhost") else host
        self.lib.rt.out(f"[WebUI] page #{self.number} is running at http://{shown}:{port}/")
        if host not in ("127.0.0.1", "localhost", "::1") and not self.props.get("password"):
            self.lib.rt.err(
                "warning: the WebUI page is open to the network without a password "
                "(add: Set Password = ...)")

    def hide(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        with self.changed:
            self.changed.notify_all()


class WebUILibrary(SurfaceLibrary, Library):
    name = "WebUI"
    summary = "Web pages with the same items as GUI, usable from a phone or another computer."
    surface_class = Page
    surface_word = "page"
    commands = (
        ("WebUI Title = My panel", "Page properties: Title, Port, Host, Password, Color"),
        ("WebUI Port = 8080", "Port of the web server (default 8080)"),
        ('Define Hello = WebUI Text "Hello"', "A text"),
        ('Define Go = WebUI Button "Toggle"', "A button"),
        ("Define Field = WebUI Input", "A box to type in"),
        ('Define Agree = WebUI Checkbox "I agree"', "A checkbox"),
        ("Define Level = WebUI Slider 50", "A slider from 0 to 100"),
        ('Define Logo = WebUI Image "https://example.com/logo.png"', "A picture from the web"),
        ("Set Hello = New text", "Change the text or value of an item"),
        ("Set Hello Color = red", "Item properties: Text, Value, Color, Background, Size, "
                                  "Enabled, X, Y, W, H, Min, Max"),
        ("{Field}", "What the item holds"),
        ('On Go "Write = clicked"', "Run a statement on a click, edit, toggle or move"),
        ("Stop Hello", "Remove an item"),
        ("Define Page = WebUI Page #1", "The page as a thing: Start Page, Stop Page"),
        ("WebUI Host = 0.0.0.0", "Let other devices connect (set a Password too)"),
        ('WebUI Password = {System Env "PAGE_PASSWORD"}', "Password asked by the browser"),
        ('WebUI Write Button "B01" on Page #1 = Toggle', "Long form: create or change by id"),
        ('{WebUI Read Input "I01" on Page #1}', "Long form: read an item by id"),
        ('On WebUI Read Button "B01" on Page #1 "Write = clicked"', "Long form: an event by id"),
    )

    def __init__(self, runtime):
        super().__init__(runtime)
        self.init_surfaces()

    def query(self, args):
        return self.surface_query(args)

    def statement(self, args, value):
        self.surface_statement(args, value)

    def subscribe(self, args, callback):
        self.surface_subscribe(args, callback)

    def ready(self):
        self.autostart()

    def alive(self):
        return any(page.server is not None for page in self.surfaces.values())

    def close(self):
        for page in self.surfaces.values():
            page.hide()
