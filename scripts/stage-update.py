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
from recovery import arm_verified_update


def main():
    parser = argparse.ArgumentParser(description="Verify and stage Sakuya release without activating it")
    parser.add_argument("commit", help="Full lowercase GitHub commit SHA")
    parser.add_argument("--activate", action="store_true", help="Arm verified release for next boot")
    args = parser.parse_args()
    lock = InstanceLock(data_directory() / "run" / "instance.lock")
    try:
        if not lock.acquire():
            parser.error("Sakuya is running. Exit via tray before staging an update.")
        directory = ROOT / "releases" / args.commit
        if not directory.exists():
            directory = stage_release(ROOT, args.commit)
        if not verify_staged_release(ROOT, args.commit):
            raise RuntimeError("Release verification failed")
        print("Verified release staged:", directory)
        if args.activate:
            arm_verified_update(ROOT, args.commit)
            print("One-boot activation armed. Start Sakuya normally to verify or roll back.")
        else:
            print("Active release unchanged. Use --activate after verification to arm one boot.")
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
