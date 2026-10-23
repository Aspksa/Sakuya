"""Release-pointer transactions. Loaded before importing any application code."""
from pathlib import Path
import json
from common import atomic_json


def release_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise ValueError("Invalid release pointer")
    path = (root / relative).resolve()
    if relative != "app" and (not relative.startswith("releases/") or ".." in Path(relative).parts):
        raise ValueError("Release must be app or releases/<id>")
    if not path.is_relative_to(root.resolve()) or not (path / "core.py").is_file():
        raise ValueError("Release is missing or outside the project")
    return path


def recover(root: Path) -> Path:
    pointer = root / "active-release.json"
    journal = root / ".updates" / "transaction.json"
    if journal.exists():
        transaction = json.loads(journal.read_text(encoding="utf-8"))
        phase = transaction["phase"]
        if phase not in ("pending", "committed"):
            raise ValueError("Unknown update transaction phase")
        target = transaction["previous"] if phase == "pending" else transaction["candidate"]
        release_path(root, target)
        atomic_json(pointer, {"path": target})
        journal.unlink()
    target = json.loads(pointer.read_text(encoding="utf-8"))["path"] if pointer.exists() else "app"
    return release_path(root, target)


def activate(root: Path, candidate: str) -> None:
    """For the future updater: caller must hold InstanceLock and verify staged hashes."""
    release_path(root, candidate)
    previous = recover(root).relative_to(root.resolve()).as_posix()
    atomic_json(root / ".updates" / "transaction.json",
                {"phase": "pending", "previous": previous, "candidate": candidate})
    atomic_json(root / "active-release.json", {"path": candidate})


def commit(root: Path) -> None:
    """Commit only after health and application checks; pending starts roll back."""
    journal = root / ".updates" / "transaction.json"
    transaction = json.loads(journal.read_text(encoding="utf-8"))
    release_path(root, transaction["candidate"])
    transaction["phase"] = "committed"
    atomic_json(journal, transaction)
