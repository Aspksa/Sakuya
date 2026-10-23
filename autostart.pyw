"""Windowed Windows Startup entry; uses exactly the same bootstrap as the BAT."""
import argparse
from pathlib import Path
import os
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--data", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent
env = os.environ.copy()
env["SAKUYA_DATA_DIR"] = args.data
ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
subprocess.Popen([str(ps), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", str(root / "scripts/start.ps1"), "-Mode", "quiet", "-Autostart"],
                 env=env, cwd=root, creationflags=subprocess.CREATE_NEW_CONSOLE)
