"""Offline integration tests for immutable, hash-verified release staging."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bootstrap"))
from release_stage import FILES, git_blob_sha, stage_release, verify_staged_release


class FakeResponse:
    status = 200
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, count):
        return self.value[:count]


class StageTests(unittest.TestCase):
    commit = "a" * 40

    def fixtures(self):
        files = {name: (b"pass\n" if name == "core.py" else name.encode()) for name in FILES}
        tree = {"truncated": False, "tree": [
            {"path": "app/" + name, "type": "blob", "mode": "100644", "sha": git_blob_sha(value)}
            for name, value in files.items()]}
        def opener(request, timeout):
            url = request.full_url
            if "/git/trees/" in url:
                return FakeResponse(json.dumps(tree).encode())
            name = url.rsplit("/", 1)[-1]
            return FakeResponse(files[name])
        return files, tree, opener

    def test_cloud_adapter_is_in_verified_release(self):
        self.assertIn("cloud_chat.py", FILES)

    def test_verified_stage_and_detect_tampering(self):
        _, _, opener = self.fixtures()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = stage_release(root, self.commit, opener)
            self.assertEqual(release.name, self.commit)
            self.assertTrue(verify_staged_release(root, self.commit))
            with self.assertRaises(FileExistsError):
                stage_release(root, self.commit, opener)
            (release / "app.js").write_bytes(b"modified")
            with self.assertRaises(ValueError):
                verify_staged_release(root, self.commit)

    def test_hash_mismatch_keeps_active_release_unchanged(self):
        _, _, opener = self.fixtures()
        def corrupted(request, timeout):
            if request.full_url.endswith("/app.js"):
                return FakeResponse(b"invalid")
            return opener(request, timeout)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "active-release.json").write_text('{"path":"app"}')
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                stage_release(root, self.commit, corrupted)
            self.assertEqual(json.loads((root / "active-release.json").read_text())["path"], "app")
            self.assertFalse((root / "releases" / self.commit).exists())

    def test_reject_incomplete_tree_and_invalid_sha(self):
        _, tree, opener = self.fixtures()
        tree["truncated"] = True
        def truncated(request, timeout):
            if "/git/trees/" in request.full_url:
                return FakeResponse(json.dumps(tree).encode())
            return opener(request, timeout)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                stage_release(Path(folder), self.commit, truncated)
            with self.assertRaises(ValueError):
                stage_release(Path(folder), "../evil", opener)


if __name__ == "__main__":
    unittest.main()
