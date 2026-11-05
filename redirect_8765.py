"""Tiny, dependency-free HTTP redirector: :8765 -> :8965.

The Shift Passdown app moved from port 8765 to 8965. Anyone with the old
port bookmarked (desktop shortcut, phone home-screen icon, old QR print-out)
would otherwise get a dead "connection refused" instead of their app.

This has no FastAPI/uvicorn dependency on purpose -- it's meant to be the
one thing that still answers on 8765 even if the real app's venv is broken,
so it's launched directly with the venv's base python.exe by the watchdog.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLD_PORT = 8765
NEW_PORT = 8965


class RedirectHandler(BaseHTTPRequestHandler):
    def _redirect(self):
        target = f"http://{self.headers.get('Host', 'localhost').split(':')[0]}:{NEW_PORT}{self.path}"
        self.send_response(307)  # keep method + body for POSTs, unlike 301/302
        self.send_header("Location", target)
        self.end_headers()

    def do_GET(self):
        self._redirect()

    def do_POST(self):
        self._redirect()

    def log_message(self, format, *args):
        pass  # keep this quiet -- it's a background shim, not a real server


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", OLD_PORT), RedirectHandler)
    server.serve_forever()
