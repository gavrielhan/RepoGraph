"""Transient localhost server: OAuth callback + repo selection page.

Security posture:
- binds to 127.0.0.1 only, on an ephemeral port
- validates the OAuth `state` parameter on the callback (CSRF)
- lives only for the duration of one `repograph activate` run; token and
  selection are held in memory and the server shuts down after the page
  posts the selection.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE_PATH = Path(__file__).parent / "selection_page.html"


class SelectionServer:
    def __init__(self):
        self.expected_state = secrets.token_urlsafe(32)
        self._code: str | None = None
        self._code_event = threading.Event()
        self._repos: list[dict] | None = None
        self._repos_ready = threading.Event()
        self._selection: list[dict] | None = None
        self._selection_event = threading.Event()

        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def select_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/select"

    def set_repos(self, repos: list[dict]) -> None:
        self._repos = repos
        self._repos_ready.set()

    def wait_for_code(self, timeout: float = 600) -> str:
        if not self._code_event.wait(timeout):
            self.shutdown()
            raise TimeoutError("timed out waiting for the GitHub OAuth callback")
        assert self._code is not None
        return self._code

    def wait_for_selection(self, timeout: float = 900) -> list[dict]:
        if not self._selection_event.wait(timeout):
            self.shutdown()
            raise TimeoutError("timed out waiting for repo selection in the browser")
        assert self._selection is not None
        return self._selection

    def shutdown(self) -> None:
        threading.Thread(target=self._httpd.shutdown, daemon=True).start()


def _make_handler(server: SelectionServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the CLI output clean
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                self._handle_callback(parse_qs(parsed.query))
            elif parsed.path in ("/", "/select"):
                self._handle_select()
            else:
                self.send_error(404)

        def do_POST(self):
            if urlparse(self.path).path == "/selection":
                self._handle_selection()
            else:
                self.send_error(404)

        # ---- routes ----

        def _handle_callback(self, params: dict):
            state = params.get("state", [""])[0]
            code = params.get("code", [""])[0]
            if state != server.expected_state or not code:
                self._respond(400, "<h1>Invalid state</h1><p>Restart repograph activate.</p>")
                return
            server._code = code
            server._code_event.set()
            self._respond(
                302, "", extra_headers=[("Location", "/select")]
            )

        def _handle_select(self):
            # wait briefly for the CLI to finish the token exchange + repo fetch
            if not server._repos_ready.wait(timeout=30):
                self._respond(
                    200,
                    "<meta http-equiv='refresh' content='2'>"
                    "<p style='font-family:sans-serif'>Fetching your repositories…</p>",
                )
                return
            html = PAGE_PATH.read_text(encoding="utf-8").replace(
                "/*__REPOS__*/[]", json.dumps(server._repos)
            )
            self._respond(200, html)

        def _handle_selection(self):
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"[]")
                assert isinstance(payload, list)
            except (json.JSONDecodeError, AssertionError):
                self.send_error(400)
                return
            server._selection = payload
            self._respond(200, json.dumps({"ok": True}), content_type="application/json")
            server._selection_event.set()

        def _respond(self, status: int, body: str, content_type: str = "text/html; charset=utf-8", extra_headers=None):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra_headers or []:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

    return Handler
