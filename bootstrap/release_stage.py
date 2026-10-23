"""Verify and stage immutable GitHub app releases; no active release mutation.

Security invariants: pinned commit SHA, Git blob digest for each allowlisted file,
no archive extraction, no executable hooks, atomic staging and no user data writes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request

OWNER_REPO = "Aspksa/Sakuya"
API = "https://api.github.com/repos/" + OWNER_REPO
FILES = ("core.py", "workspace.py", "index.html", "app.js", "app.css", "favicon.svg")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEADERS = {"User-Agent": "Sakuya-Release-Stager/1", "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28"}
MAX_FILE = 1024 * 1024
MAX_TREE = 2 * 1024 * 1024


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def read_https(url: str, limit: int, opener=None) -> bytes:
    if not url.startswith("https://api.github.com/") and not url.startswith("https://raw.githubusercontent.com/"):
        raise ValueError("Unapproved update host")
    opener = opener or urllib.request.urlopen
    with opener(urllib.request.Request(url, headers=HEADERS), timeout=12) as response:
        if response.status != 200:
            raise ValueError("Unexpected GitHub status")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Update response exceeds size limit")
    return content


def verify_tree(tree: dict) -> dict:
    if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise ValueError("Incomplete GitHub tree")
    blobs = {}
    for entry in tree["tree"]:
        if not isinstance(entry, dict):
            raise ValueError("Invalid tree entry")
        path = entry.get("path")
        if path in ("app/" + filename for filename in FILES):
            if entry.get("type") != "blob" or entry.get("mode") != "100644":
                raise ValueError("Unsafe release file mode")
            sha = entry.get("sha")
            if not isinstance(sha, str) or not HEX40.fullmatch(sha) or path in blobs:
                raise ValueError("Invalid or duplicate release file")
            blobs[path] = sha
    if set(blobs) != {"app/" + filename for filename in FILES}:
        raise ValueError("Incomplete application release")
    return blobs


def stage_release(root: Path, commit: str, opener=None) -> Path:
    """Only call while holding the supervisor's exclusive instance lock."""
    if not isinstance(commit, str) or not HEX40.fullmatch(commit):
        raise ValueError("A full, lowercase commit SHA is required")
    root = root.resolve()
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / commit
    if target.exists():
        raise FileExistsError("Release already exists; refusing to overwrite it")
    tree = json.loads(read_https(API + "/git/trees/" + commit + "?recursive=1", MAX_TREE, opener))
    blobs = verify_tree(tree)
    temporary = Path(tempfile.mkdtemp(prefix=".stage-", dir=releases))
    try:
        for filename in FILES:
            path = "app/" + filename
            url = "https://raw.githubusercontent.com/" + OWNER_REPO + "/" + commit + "/" + path
            data = read_https(url, MAX_FILE, opener)
            if git_blob_sha(data) != blobs[path]:
                raise ValueError("Git blob hash mismatch for " + filename)
            (temporary / filename).write_bytes(data)
        # Reject syntax-invalid Python before publishing the release directory.
        compile((temporary / "core.py").read_bytes(), str(temporary / "core.py"), "exec")
        manifest = {"commit": commit, "files": {
            filename: hashlib.sha256((temporary / filename).read_bytes()).hexdigest()
            for filename in FILES}}
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def verify_staged_release(root: Path, commit: str) -> bool:
    if not isinstance(commit, str) or not HEX40.fullmatch(commit):
        raise ValueError("Invalid release ID")
    root = root.resolve()
    directory = root / "releases" / commit
    if directory.is_symlink() or not directory.is_dir() or directory.resolve() != (root / "releases" / commit).absolute():
        raise ValueError("Invalid release directory")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("commit") != commit or set(manifest.get("files", {})) != set(FILES):
        raise ValueError("Invalid release manifest")
    for filename in FILES:
        path = directory / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError("Invalid release file")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != manifest["files"][filename]:
            raise ValueError("Release file was modified")
    return True
