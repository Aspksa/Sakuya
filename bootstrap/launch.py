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
from display import StartupDisplay, ConsoleLogHandler

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


def configure_logger(data, display):
    (data / "logs").mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sakuya")
    logger.setLevel(logging.DEBUG if display.diagnostic else logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(data / "logs/sakuya.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    stream = ConsoleLogHandler(display)
    stream.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stream)
    return logger


def run(mode="normal", autostart=False):
    messages = json.loads((ROOT / "config/messages.ru.json").read_text(encoding="utf-8"))
    display = StartupDisplay(messages, mode == "diagnostic")
    data = data_directory()
    data.mkdir(parents=True, exist_ok=True)
    logger = configure_logger(data, display)
    lock = InstanceLock(data / "run/instance.lock")
    stop = threading.Event()
    server = thread = tray = None
    state = None
    settings = None
    owned = False
    hidden = False
    core = None
    previous_handlers = {}
    advice = messages["console"]["help_instance"]
    try:
        display.begin(messages["console"]["stage_instance"])
        owned = lock.acquire()
        if not owned:
            logger.info(messages["waiting"])
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    running = read_instance(data, instance_id(data))
                    logger.info(messages["already"])
                    display.end()
                    display.summary(running["url"], mode)
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
        display.end()

        # Acquire the OS lock BEFORE recovery, imports or mutations to program state.
        display.begin(messages["console"]["stage_recovery"])
        advice = messages["console"]["help_release"]
        release = recover(ROOT)
        spec = importlib.util.spec_from_file_location("sakuya_core", release / "core.py")
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        advice = messages["console"]["help_settings"].format(data / "settings.json")
        settings_file = data / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
        if not isinstance(settings, dict):
            raise ValueError(messages["console"]["bad_settings"])
        settings.setdefault("port", 0)
        settings.setdefault("autostart_browser", False)
        if not isinstance(settings["autostart_browser"], bool):
            raise ValueError("autostart_browser должен быть true или false")
        if type(settings["port"]) is not int or not 0 <= settings["port"] <= 65535:
            raise ValueError("port должен быть целым числом от 0 до 65535")
        core.save(data, settings)
        (data / "documents").mkdir(exist_ok=True)
        display.end()
        session, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        advice = messages["console"]["help_server"]
        display.begin(messages["console"]["stage_server"])
        server = core.create_server(data, instance_id(data), session, token, stop, settings, logger)
        thread = threading.Thread(target=server.serve_forever, name="sakuya-http", daemon=True)
        thread.start()
        state = {"url": f"http://127.0.0.1:{server.server_port}", "pid": os.getpid(),
                 "session": session, "instance_id": instance_id(data), "token": token}
        atomic_json(data / "run/instance.json", state)
        display.end()
        advice = messages["console"]["help_health"]
        display.begin(messages["console"]["stage_health"])
        deadline = time.monotonic() + 15
        while True:
            try:
                read_instance(data, instance_id(data))
                break
            except (OSError, ValueError, KeyError):
                if time.monotonic() >= deadline or not thread.is_alive():
                    raise RuntimeError("Сервер не подтвердил готовность за 15 секунд")
                time.sleep(.1)
        display.end()
        logger.info("%s: %s; PID=%s; Python=%s", messages["ready"], state["url"], os.getpid(), sys.executable)
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                previous_handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        try:
            display.begin(messages["console"]["stage_tray"])
            tray = start_tray(data, state, logger, messages)
        except Exception:
            logger.exception(messages["tray_error"])
        display.end("success" if tray is not None else "warning")
        total = display.summary(state["url"], mode)
        logger.info("Итог запуска: адрес=%s; режим=%s; время=%.2f с; этапы=%s", state["url"], mode, total, display.timings)
        if mode == "diagnostic":
            logger.info(messages["diagnostic"])
        elif tray is None:
            console(True)
            display.refresh()
            logger.warning(messages["fallback"])
            display.notice(messages["console"]["log"].format(data / "logs/tray.log"), "brand")
        else:
            console(False)
            hidden = True
        if os.environ.get("SAKUYA_NO_BROWSER") != "1" and (not autostart or settings["autostart_browser"]):
            open_browser(state["url"], logger)
        advice = messages["console"]["help_unknown"]
        while not stop.wait(.25):
            if not thread.is_alive():
                raise RuntimeError("Сервер неожиданно остановился")
            if tray is not None and tray.poll() is not None:
                console(True)
                display.refresh()
                hidden = False
                logger.warning(messages["fallback"])
                tray = None
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        console(True)
        display.refresh()
        hidden = False
        if isinstance(exc, PermissionError):
            advice = messages["console"]["help_permission"]
        display.error(exc, advice, data / "logs/sakuya.log")
        logger.exception("%s. %s: %s", messages["error"], messages["log"], data / "logs/sakuya.log", extra={"file_only": True})
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
        except Exception as exc:
            console(True)
            display.refresh()
            hidden = False
            display.error(exc, messages["console"]["help_permission"] if isinstance(exc, PermissionError)
                          else messages["console"]["help_unknown"], data / "logs/sakuya.log")
            logger.exception("Ошибка завершения Sakuya", extra={"file_only": True})
            return 1
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            lock.close()
            # Show the original BAT console before it exits, including on save failures.
            if hidden:
                console(True)
            display.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("normal", "diagnostic", "quiet"), default="normal")
    parser.add_argument("--autostart", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.mode, args.autostart))
