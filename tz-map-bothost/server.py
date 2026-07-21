import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(BASE, "build")
ROOT = BUILD if os.path.isfile(os.path.join(BUILD, "index.html")) else BASE
MANTLE_BASE = "https://mantledb.sh/v2/tzmap-public"
MANTLE_KEY = ""
VIS_BASE = "https://mantledb.sh/v2/visibility/tzmap-public"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def _proxy(self, url, method, body=None):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        if MANTLE_KEY:
            headers["X-Mantle-Key"] = MANTLE_KEY
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_body or b"{}")
        except Exception as e:
            msg = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(msg)

    def _webhook_ok(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_OPTIONS(self):
        if self.path.startswith("/api/sync/"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Mantle-Key")
            self.end_headers()
            return
        super().do_OPTIONS()

    def do_GET(self):
        if self.path in ("/health", "/health/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path in ("/", "/index.html"):
            self.path = "/index.html"
        if self.path.startswith("/api/sync/rooms/"):
            room = self.path.split("/api/sync/rooms/", 1)[1].split("?", 1)[0]
            self._proxy(f"{MANTLE_BASE}/rooms/{urllib.parse.quote(room, safe='/')}", "GET")
            return
        super().do_GET()

    def do_POST(self):
        if self.path in ("/webhook", "/webhook/"):
            self._webhook_ok()
            return
        if self.path.startswith("/api/sync/rooms/"):
            room = self.path.split("/api/sync/rooms/", 1)[1].split("?", 1)[0]
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            self._proxy(f"{MANTLE_BASE}/rooms/{urllib.parse.quote(room, safe='/')}", "POST", body)
            return
        super().do_POST()

    def do_PATCH(self):
        if self.path.startswith("/api/sync/rooms/"):
            room = self.path.split("/api/sync/rooms/", 1)[1].split("?", 1)[0]
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            self._proxy(f"{MANTLE_BASE}/rooms/{urllib.parse.quote(room, safe='/')}", "PATCH", body)
            return
        self.send_error(501, "Unsupported method")

    def do_DELETE(self):
        if self.path.startswith("/api/sync/rooms/"):
            room = self.path.split("/api/sync/rooms/", 1)[1].split("?", 1)[0]
            self._proxy(f"{MANTLE_BASE}/rooms/{urllib.parse.quote(room, safe='/')}", "DELETE", None)
            return
        self.send_error(501, "Unsupported method")

    def do_PUT(self):
        if self.path.startswith("/api/sync/visibility/"):
            room = self.path.split("/api/sync/visibility/", 1)[1].split("?", 1)[0]
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b'{"public_read":true}'
            self._proxy(f"{VIS_BASE}/rooms/{urllib.parse.quote(room, safe='/')}", "PUT", body)
            return
        super().do_PUT()


def _serve(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Listening on 0.0.0.0:{port} (root={ROOT})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    primary = int(os.environ.get("PORT", "3000"))
    ports = []
    for p in (primary, 8080, 3000):
        if p not in ports:
            ports.append(p)
    print(f"TZ map root: {ROOT}", flush=True)
    print("Sync proxy: /api/sync/ -> mantledb.sh", flush=True)
    for port in ports:
        if port == primary:
            continue
        threading.Thread(target=_serve, args=(port,), daemon=True).start()
    _serve(primary)
