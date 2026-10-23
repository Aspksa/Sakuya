"""Exercise armed release, successful acknowledgement and rollback paths."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bootstrap"))
from recovery import arm_verified_update, commit, recover
from release_stage import FILES

SHA = "a" * 40


class ArmedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "app").mkdir()
        (self.root / "app/core.py").write_text("pass")
        target = self.root / "releases" / SHA
        target.mkdir(parents=True)
        files = {}
        for name in FILES:
            data = b"pass\n" if name == "core.py" else name.encode()
            (target / name).write_bytes(data)
            files[name] = hashlib.sha256(data).hexdigest()
        (target / "manifest.json").write_text(json.dumps({"commit": SHA, "files": files}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_successful_boot_commits(self):
        arm_verified_update(self.root, SHA)
        self.assertEqual(recover(self.root), self.root / "releases" / SHA)
        commit(self.root)
        self.assertEqual(recover(self.root), self.root / "releases" / SHA)

    def test_crashed_first_boot_rolls_back(self):
        arm_verified_update(self.root, SHA)
        self.assertEqual(recover(self.root), self.root / "releases" / SHA)
        self.assertEqual(recover(self.root), self.root / "app")
        self.assertEqual(recover(self.root), self.root / "app")

    def test_modified_candidate_blocks_activation(self):
        (self.root / "releases" / SHA / "app.js").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            arm_verified_update(self.root, SHA)
        self.assertEqual(recover(self.root), self.root / "app")


if __name__ == "__main__":
    unittest.main()
