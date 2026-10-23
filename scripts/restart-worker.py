"""Detached restart worker, waits for exclusive instance lock before launching."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bootstrap"))
from common import InstanceLock, data_directory


def wait_for_exit(data, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lock = InstanceLock(data / "run" / "instance.lock")
        try:
            if lock.acquire():
                return True
        finally:
            lock.close()
        time.sleep(.2)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-pid", required=True, type=int)
    args = parser.parse_args()
    if args.owner_pid <= 0 or os.environ.get("SAKUYA_ROOT") != str(ROOT):
        return 2
    data = data_directory()
    if not wait_for_exit(data):
        return 3
    # Detached child starts the same stable supervisor, not untrusted release code.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    with (data / "logs" / "restart.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "bootstrap" / "launch.py")],
                         cwd=str(ROOT), env=os.environ.copy(), stdout=log, stderr=log,
                         creationflags=flags, close_fds=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
