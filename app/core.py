"""Minimal local application. No third-party runtime dependencies."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import threading
from common import APP_ID, APP_NAME, atomic_json


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def create_server(data: Path, identity: str, session: str, token: str,
                  stop: threading.Event, settings: dict, logger):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def reply(self, code, value, content_type="application/json; charset=utf-8"):
            body = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {"error": "Invalid host"})
            if self.path == "/api/health":
                return self.reply(200, {"app": APP_NAME, "app_id": APP_ID, "instance_id": identity,
                                       "session": session, "pid": os.getpid(), "ready": not stop.is_set()})
            if self.path == "/":
                return self.reply(200, (Path(__file__).parent / "index.html").read_bytes(), "text/html; charset=utf-8")
            self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if (not self.valid_host() or self.headers.get("Origin") is not None
                    or not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)):
                return self.reply(403, {"error": "Forbidden"})
            if self.path == "/api/shutdown":
                self.reply(200, {"stopping": True})
                stop.set()
            else:
                self.reply(404, {"error": "Not found"})

        def log_message(self, fmt, *args):
            logger.debug("HTTP " + fmt, *args)

    preferred = settings.get("port", 0)
    if not isinstance(preferred, int) or not 0 <= preferred <= 65535:
        raise ValueError("Некорректный порт в settings.json")
    try:
        server = LocalServer(("127.0.0.1", preferred), Handler)
    except OSError as exc:
        import errno
        if not preferred or (exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048):
            raise
        logger.info("Порт %s занят, выбираю свободный", preferred)
        server = LocalServer(("127.0.0.1", 0), Handler)
    return server


def save(data: Path, settings: dict):
    atomic_json(data / "settings.json", settings)
