"""The game's server, without a network: used by the browser-only build (see build_pages.py).

In that build the Python game runs inside the player's browser (Pyodide). The page's fetch() calls are handed to handle() here,
which runs the very same request code the local server uses (webapp.Handler) and returns the answer as JSON.
Nothing about the rules or the game state is different from `python webapp.py`.
"""

import json
import urllib.parse

import webapp


class _Headers(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _MemHandler(webapp.Handler):
    """A webapp.Handler that reads from a string and keeps its answer in memory instead of using a socket."""

    def __init__(self, method, path, body, headers):          # noqa: super().__init__ would wait on a socket
        self.command, self.path, self.headers = method, path, _Headers(headers)
        self._body = body
        self.status, self.ctype, self.payload, self.location = 200, "text/plain", b"", None

    def _read_form(self):
        parsed = urllib.parse.parse_qs(self._body or "")
        return {k: v[0] for k, v in parsed.items()}

    def _send_html(self, body, status=200):
        self.status, self.ctype, self.payload = status, "text/html; charset=utf-8", body.encode("utf-8")

    def _send_json(self, obj, status=200):
        self.status, self.ctype, self.payload = status, "application/json; charset=utf-8", json.dumps(obj, separators=(",", ":")).encode("utf-8")

    def _redirect(self, path="/"):
        if self._wants_json():
            return super()._redirect(path)
        self.status, self.location = 303, path


def handle(method, path, body="", headers_json="{}"):
    """Answer one request. Returns a JSON string {status, ctype, body, location}."""
    h = _MemHandler(method, path, body, json.loads(headers_json or "{}"))
    h._guarded(h._handle_get if method == "GET" else h._handle_post)
    return json.dumps({"status": h.status, "ctype": h.ctype, "body": h.payload.decode("utf-8"), "location": h.location})


def tick():
    """The room referee's once-a-second check (who dropped out, what has stalled). The real server runs this in a thread; in the browser the page calls it."""
    with webapp._LOCK:
        webapp.ROOMS.sweep()
        webapp.room_tick()
    return "ok"
