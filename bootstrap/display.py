"""Console-only startup presentation. Redirected output never contains control codes."""
from __future__ import annotations

import ctypes
import logging
import math
import os
import re
import sys
import time


class StartupDisplay:
    COLORS = {"brand": "38;5;179", "running": "36", "success": "32", "warning": "93", "error": "91"}
    SYMBOLS = {"success": "✓", "warning": "⚠", "error": "✕"}

    def __init__(self, messages, diagnostic=False, stream=None, clock=time.perf_counter):
        self.text = messages["console"]
        self.diagnostic = diagnostic
        self._stream = stream
        self.clock = clock
        self.started = clock()
        self.active = None
        self.timings = []
        self._original_mode = None
        self._handle = None
        self.refresh()

    @property
    def stream(self):
        return self._stream if self._stream is not None else sys.stdout

    def refresh(self):
        stream = self.stream
        self.color = bool(stream and stream.isatty() and os.environ.get("TERM") != "dumb")
        if self.color and os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.argtypes = [ctypes.c_uint32]
            kernel.GetStdHandle.restype = ctypes.c_void_p
            kernel.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            kernel.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            handle = kernel.GetStdHandle(-11 & 0xffffffff)
            mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)) and kernel.SetConsoleMode(handle, mode.value | 4):
                if self._original_mode is None:
                    self._handle, self._original_mode = handle, mode.value
            else:
                self.color = False
        self.rewrite = self.color and not self.diagnostic

    @staticmethod
    def clean(value):
        # Exception messages and paths must not inject console escapes or cursor moves.
        return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))

    def _write(self, text, kind="running", end="\n"):
        if self.stream is None:
            return
        text = self.clean(text)
        if self.color:
            text = f"\x1b[{self.COLORS[kind]}m{text}\x1b[0m"
        self.stream.write(text + end)
        self.stream.flush()

    def _erase(self):
        if self.stream is not None and self.rewrite:
            self.stream.write("\r\x1b[2K")

    def begin(self, name):
        if self.active is not None:
            raise RuntimeError("Previous startup stage is still active")
        self.active = (name, self.clock())
        self._write(self.text["running"].format(name), end="" if self.rewrite else "\n")

    def end(self, status="success"):
        if self.active is None:
            return None
        name, started = self.active
        elapsed = max(0, self.clock() - started)
        self._erase()
        self._write(self.text["finished"].format(self.SYMBOLS[status], name, f"{elapsed:.2f}"), status)
        self.timings.append({"stage": name, "seconds": elapsed, "status": status})
        self.active = None
        return elapsed

    def notice(self, message, kind="warning"):
        self._erase()
        self._write(message, kind)
        if self.active is not None and self.rewrite:
            self._write(self.text["running"].format(self.active[0]), end="")

    def error(self, exception, advice, log):
        self.end("error")
        self._write(self.text["failure"], "error")
        self._write(self.text["reason"].format(str(exception) or type(exception).__name__), "error")
        self._write(self.text["action"].format(advice), "warning")
        self._write(self.text["log"].format(log), "brand")

    def summary(self, url, mode):
        total = max(0, self.clock() - self.started)
        # Includes BAT/PowerShell preparation and interpreter startup when available.
        try:
            start = float(os.environ["SAKUYA_LAUNCH_STARTED_AT"])
            from_bootstrap = time.time() - start
            if math.isfinite(from_bootstrap) and 0 <= from_bootstrap <= 120:
                total = from_bootstrap
        except (KeyError, ValueError):
            pass
        self._write(self.text["summary"].format(f"{total:.2f}"), "success")
        self._write(self.text["address"].format(url), "brand")
        self._write(self.text["mode"].format(self.text["modes"][mode]), "brand")
        return total

    def close(self):
        if self._original_mode is not None:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.SetConsoleMode(self._handle, self._original_mode)


class ConsoleLogHandler(logging.Handler):
    def __init__(self, display):
        super().__init__()
        self.display = display

    def emit(self, record):
        # Full tracebacks stay in the file; diagnostic mode also prints them.
        if getattr(record, "file_only", False) and not self.display.diagnostic:
            return
        if not self.display.diagnostic and record.levelno < logging.WARNING:
            return
        kind = "error" if record.levelno >= logging.ERROR else "warning" if record.levelno >= logging.WARNING else "running"
        text = self.format(record) if self.display.diagnostic else record.getMessage()
        if self.display.diagnostic and record.exc_info:
            # Preserve traceback line breaks without allowing control sequences from messages.
            for line in text.splitlines():
                self.display.notice(line, kind)
        else:
            self.display.notice(text, kind)
