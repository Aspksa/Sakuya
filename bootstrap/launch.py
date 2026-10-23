"""Single supervisor shared by BAT, diagnostic, quiet and Windows autostart."""
from __future__ import annotations

import sys
from pathlib import Path

# Embeddable Python deliberately ignores script directories in isolated mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse
import ctypes
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import signal
import subprocess
import threading
import time
import urllib.request
import webbrowser

from common import HTTP, InstanceLock, atomic_json, data_directory, instance_id, read_instance
from recovery import recover

ROOT = Path(__file__).resolve().parents[1]


def console(show: bool):
    if os.name != "nt":
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    window = kernel.GetConsoleWindow()
    if show and not window:
        kernel.AllocConsole()
        window = kernel.GetConsoleWindow()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    if window:
        user.ShowWindow(window, 5 if show else 0)


def open_browser(url, logger):
    if os.environ.get("SAKUYA_NO_BROWSER") == "1":
        return
    try:
        if not webbrowser.open(url):
            logger.warning("Не удалось автоматически открыть браузер. Адрес: %s", url)
    except Exception:
        logger.exception("Не удалось открыть браузер. Адрес: %s", url)


def start_tray(data, state, logger, messages):
    if os.name != "nt" or os.environ.get("SAKUYA_DISABLE_TRAY") == "1":
        return None
    env = os.environ.copy()
    env.update({"SAKUYA_DATA_DIR": str(data), "SAKUYA_ROOT": str(ROOT),
                "SAKUYA_SESSION": state["session"], "SAKUYA_OWNER_PID": str(os.getpid())})
    ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    with (data / "logs" / "tray.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen([str(ps), "-NoLogo", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
                                    "-File", str(ROOT / "scripts/tray.ps1")],
                                   env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and process.poll() is None:
        try:
            ready = json.loads((data / "run/tray-ready.json").read_text(encoding="utf-8-sig"))
            if ready["session"] == state["session"] and ready["pid"] == process.pid:
                logger.info(messages["tray_ready"], process.pid)
                return process
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.1)
    logger.warning(messages["tray_timeout"])
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    return None


def configure_logger(data, diagnostic):
    (data / "logs").mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sakuya")
    logger.setLevel(logging.DEBUG if diagnostic else logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(data / "logs/sakuya.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    if sys.stdout is not None:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream)
    return logger


def run(mode="normal", autostart=False):
    data = data_directory()
    data.mkdir(parents=True, exist_ok=True)
    logger = configure_logger(data, mode == "diagnostic")
    messages = json.loads((ROOT / "config/messages.ru.json").read_text(encoding="utf-8"))
    lock = InstanceLock(data / "run/instance.lock")
    stop = threading.Event()
    server = thread = tray = None
    state = None
    settings = None
    owned = False
    hidden = False
    core = None
    previous_handlers = {}
    try:
        owned = lock.acquire()
        if not owned:
            logger.info(messages["waiting"])
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    running = read_instance(data, instance_id(data))
                    logger.info(messages["already"])
                    if not autostart or json.loads((data / "settings.json").read_text(encoding="utf-8")).get("autostart_browser", False):
                        open_browser(running["url"], logger)
                    return 0
                except (OSError, ValueError, KeyError):
                    if lock.acquire():
                        owned = True
                        break
                    time.sleep(.2)
            if not owned:
                raise RuntimeError("Экземпляр занят, но не подтвердил готовность за 30 секунд. См. журнал.")

        # Acquire the OS lock BEFORE recovery, imports or mutations to program state.
        release = recover(ROOT)
        spec = importlib.util.spec_from_file_location("sakuya_core", release / "core.py")
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        settings_file = data / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
        settings.setdefault("port", 0)
        settings.setdefault("autostart_browser", False)
        if not isinstance(settings["autostart_browser"], bool):
            raise ValueError("autostart_browser должен быть true или false")
        core.save(data, settings)
        (data / "documents").mkdir(exist_ok=True)
        session, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        server = core.create_server(data, instance_id(data), session, token, stop, settings, logger)
        thread = threading.Thread(target=server.serve_forever, name="sakuya-http", daemon=True)
        thread.start()
        state = {"url": f"http://127.0.0.1:{server.server_port}", "pid": os.getpid(),
                 "session": session, "instance_id": instance_id(data), "token": token}
        atomic_json(data / "run/instance.json", state)
        deadline = time.monotonic() + 15
        while True:
            try:
                read_instance(data, instance_id(data))
                break
            except (OSError, ValueError, KeyError):
                if time.monotonic() >= deadline or not thread.is_alive():
                    raise RuntimeError("Сервер не подтвердил готовность за 15 секунд")
                time.sleep(.1)
        logger.info("%s: %s; PID=%s; Python=%s", messages["ready"], state["url"], os.getpid(), sys.executable)
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                previous_handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        try:
            tray = start_tray(data, state, logger, messages)
        except Exception:
            logger.exception(messages["tray_error"])
        if mode == "diagnostic":
            logger.info(messages["diagnostic"])
        elif tray is None:
            console(True)
            logger.warning(messages["fallback"])
        else:
            console(False)
            hidden = True
        if os.environ.get("SAKUYA_NO_BROWSER") != "1" and (not autostart or settings["autostart_browser"]):
            open_browser(state["url"], logger)
        while not stop.wait(.25):
            if not thread.is_alive():
                raise RuntimeError("Сервер неожиданно остановился")
            if tray is not None and tray.poll() is not None:
                console(True)
                hidden = False
                logger.warning(messages["fallback"])
                tray = None
        return 0
    except (Exception, KeyboardInterrupt):
        console(True)
        hidden = False
        logger.exception("%s. %s: %s", messages["error"], messages["log"], data / "logs/sakuya.log")
        return 1
    finally:
        try:
            stop.set()
            if server is not None:
                if thread is not None and thread.is_alive():
                    server.shutdown()
                server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
            if tray is not None:
                (data / "run" / ("tray-stop-" + state["session"])).touch()
                try:
                    tray.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    tray.terminate()
                    tray.wait(timeout=5)
            if owned:
                if state is not None and settings is not None and core is not None:
                    core.save(data, settings)
                if state is not None:
                    (data / "run/instance.json").unlink(missing_ok=True)
                    (data / "run/tray-ready.json").unlink(missing_ok=True)
                    (data / "run" / ("tray-stop-" + state["session"])).unlink(missing_ok=True)
                logger.info(messages["stopped"])
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            lock.close()
            # Show the original BAT console before it exits, including on save failures.
            if hidden:
                console(True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("normal", "diagnostic", "quiet"), default="normal")
    parser.add_argument("--autostart", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.mode, args.autostart))
