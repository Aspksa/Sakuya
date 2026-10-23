"""Explicit opt-in current-user Windows autostart, no administrator rights."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import json
import os
import subprocess
from common import data_directory


def expected_command():
    root = Path(__file__).resolve().parents[1]
    windowed = Path(sys.executable).with_name("pythonw.exe")
    return subprocess.list2cmdline([str(windowed.resolve()), "-X", "utf8", str(root / "autostart.pyw"),
                                   "--data", str(data_directory())])


def status():
    if os.name != "nt":
        return {"supported": False, "enabled": False, "matches": False}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            command, _ = winreg.QueryValueEx(key, "Sakuya.aspksa.sakuya")
    except FileNotFoundError:
        command = None
    return {"supported": True, "enabled": command is not None, "matches": command == expected_command()}


def configure(action, quiet=False):
    if os.name != "nt":
        raise RuntimeError("Автозапуск доступен только в Windows")
    import winreg
    root = Path(__file__).resolve().parents[1]
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        if action == "disable":
            try:
                winreg.DeleteValue(key, "Sakuya.aspksa.sakuya")
            except FileNotFoundError:
                pass
        else:
            windowed = Path(sys.executable).with_name("pythonw.exe")
            if not windowed.is_file():
                raise RuntimeError("Для автозапуска нужен pythonw.exe рядом с выбранным Python")
            # Probe windowed interpreter; it must create the expected result, not merely exist.
            import tempfile
            with tempfile.TemporaryDirectory() as directory:
                result = Path(directory) / "probe.json"
                code = "import sys,json,pathlib; pathlib.Path(sys.argv[1]).write_text(json.dumps(list(sys.version_info[:2])))"
                process = subprocess.run([str(windowed), "-X", "utf8", "-c", code, str(result)], timeout=10)
                if process.returncode or json.loads(result.read_text()) != list(sys.version_info[:2]):
                    raise RuntimeError("Проверка pythonw.exe завершилась ошибкой")
            command = expected_command()
            winreg.SetValueEx(key, "Sakuya.aspksa.sakuya", 0, winreg.REG_SZ, command)
    if not quiet:
        print("Автозапуск Sakuya " + ("включён." if action == "enable" else "выключен."))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("enable", "disable"):
        raise SystemExit("Expected enable or disable")
    configure(sys.argv[1])
