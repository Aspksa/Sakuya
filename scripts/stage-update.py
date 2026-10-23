"""Offline staging command. Run only after Sakuya has been fully stopped.

Usage: python scripts/stage-update.py <40-character-commit-sha>
This command never activates downloaded code.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bootstrap"))
from common import InstanceLock, data_directory
from release_stage import stage_release, verify_staged_release


def main():
    parser = argparse.ArgumentParser(description="Verify and stage Sakuya release without activating it")
    parser.add_argument("commit", help="Full lowercase GitHub commit SHA")
    args = parser.parse_args()
    lock = InstanceLock(data_directory() / "run" / "instance.lock")
    try:
        if not lock.acquire():
            parser.error("Sakuya is running. Exit via tray before staging an update.")
        directory = stage_release(ROOT, args.commit)
        if not verify_staged_release(ROOT, args.commit):
            raise RuntimeError("Release verification failed")
        print("Verified release staged:", directory)
        print("Active release unchanged. Installation is not yet supported.")
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
