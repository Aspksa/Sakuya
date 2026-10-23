"""Stable bootstrap utilities; no imports from the replaceable application."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

APP_ID = "aspksa.sakuya"
APP_NAME = "Sakuya"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def data_directory() -> Path:
    explicit = os.environ.get("SAKUYA_DATA_DIR")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            raise ValueError("SAKUYA_DATA_DIR must be absolute")
        return path.resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "Sakuya"


def instance_id(data: Path) -> str:
    return APP_ID + "." + hashlib.sha256(os.path.normcase(str(data.resolve())).encode()).hexdigest()[:20]


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # Antivirus scanners can transiently open the destination on Windows.
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.05)
    finally:
        Path(tmp).unlink(missing_ok=True)


class InstanceLock:
    """An OS byte-range lock, released even after terminate/kill. Never unlink it."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.acquired = False

    def acquire(self) -> bool:
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self.acquired = True
        return True

    def close(self):
        if self.acquired:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


def read_instance(data: Path, expected: str):
    state = json.loads((data / "run" / "instance.json").read_text(encoding="utf-8"))
    parts = urlsplit(state["url"])
    if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port
            or parts.path or parts.query or parts.fragment or parts.username):
        raise ValueError("Invalid local server address")
    with HTTP.open(state["url"] + "/api/health", timeout=.7) as response:
        health = json.load(response)
    if (health.get("app") != APP_NAME or health.get("app_id") != APP_ID
            or health.get("instance_id") != expected or health.get("pid") != state["pid"]
            or health.get("session") != state["session"] or health.get("ready") is not True):
        raise ValueError("Health identity mismatch")
    return state
