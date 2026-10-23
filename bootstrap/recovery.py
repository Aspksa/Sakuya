"""Release-pointer transactions. Loaded before importing any application code."""
from pathlib import Path
import json
from common import atomic_json
from release_stage import verify_staged_release


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
        if phase not in ("armed", "pending", "committed"):
            raise ValueError("Unknown update transaction phase")
        if phase == "armed":
            candidate = transaction["candidate"]
            previous = transaction["previous"]
            if not candidate.startswith("releases/") or not verify_staged_release(root, candidate.split("/", 1)[1]):
                raise ValueError("Staged release verification failed")
            release_path(root, previous)
            release_path(root, candidate)
            # Record attempted boot before changing the active pointer.
            atomic_json(journal, dict(transaction, phase="pending"))
            atomic_json(pointer, {"path": candidate})
            return release_path(root, candidate)
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

def arm_verified_update(root: Path, commit_sha: str) -> None:
    """Schedule one candidate boot while holding the exclusive InstanceLock."""
    from release_stage import HEX40
    if not isinstance(commit_sha, str) or not HEX40.fullmatch(commit_sha):
        raise ValueError("Invalid release commit")
    if not verify_staged_release(root, commit_sha):
        raise ValueError("Candidate failed verification")
    journal = root / ".updates" / "transaction.json"
    if journal.exists():
        raise ValueError("Finish or recover previous update first")
    previous = recover(root).relative_to(root.resolve()).as_posix()
    candidate = "releases/" + commit_sha
    if previous == candidate:
        raise ValueError("This release is already active")
    atomic_json(journal, {"phase": "armed", "previous": previous, "candidate": candidate})
