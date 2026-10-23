"""Test remote update checks without network access."""
import io
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bootstrap"))
import updater


class Response:
    status = 200
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, amount):
        return self.data[:amount]


class UpdaterTests(unittest.TestCase):
    def test_commit_metadata(self):
        def fake_open(request, timeout):
            self.assertEqual(timeout, 6)
            self.assertEqual(request.full_url, updater.API)
            return Response(json.dumps({"sha": "a"*40}).encode())
        result = updater.check_remote(opener=fake_open)
        self.assertTrue(result["reachable"])
        self.assertEqual(result["commit"], "a"*40)
        self.assertFalse(result["installation_supported"])

    def test_reject_bad_sha_and_oversized_reply(self):
        for data in [b'{"sha":"no"}', b'X'*65537]:
            with self.subTest(data=data[:8]):
                with self.assertRaises(ValueError):
                    updater.check_remote(opener=lambda *a, **k: Response(data))

    def test_private_repo_does_not_expose_token(self):
        def forbidden(request, timeout):
            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO())
        result = updater.check_remote(opener=forbidden)
        self.assertFalse(result["reachable"])
        self.assertFalse(result["installation_supported"])


if __name__ == "__main__":
    unittest.main()
