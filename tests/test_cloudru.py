"""Offline Cloud.ru integration checks. No real API keys or network requests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bootstrap"))
import cloudru


class Response:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size):
        return self.payload[:size]


class CloudruTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_models_and_bearer_auth(self):
        def opener(request, timeout):
            self.assertEqual(timeout, 12)
            self.assertEqual(request.full_url, cloudru.BASE + "/models")
            self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
            return Response(json.dumps({"data":[{"id":"test/model"},{"id":"model-2"},{"id":"test/model"}]}).encode())
        with patch.object(cloudru, "load_key", return_value="secret-key"):
            self.assertEqual(cloudru.fetch_models(self.data, opener), ["model-2","test/model"])

    def test_non_chat_models_are_filtered(self):
        def opener(request, timeout):
            return Response(json.dumps({"data":[
                {"id":"openai/gpt-oss-120b","metadata":{"type":"LLM"}},
                {"id":"openai/whisper-large-v3","metadata":{"type":"Audio-to-Text"}},
                {"id":"BAAI/bge-reranker-v2-m3","metadata":{"type":"Rerank"}}
            ]}).encode())
        with patch.object(cloudru,"load_key",return_value="secret-key"):
            self.assertEqual(cloudru.fetch_models(self.data,opener),["openai/gpt-oss-120b"])

    def test_no_key_in_configuration(self):
        cloudru.set_model(self.data,"test/model")
        self.assertEqual(cloudru.get_config(self.data)["model"],"test/model")
        self.assertNotIn("secret",cloudru.config_path(self.data).read_text())
        with self.assertRaises(ValueError):
            cloudru.set_model(self.data,"../ model with spaces")

    def test_reject_invalid_response(self):
        with patch.object(cloudru,"load_key",return_value="secret-key"):
            with self.assertRaises(ValueError):
                cloudru.fetch_models(self.data,lambda request,timeout:Response(b'{"data":{}}'))

    def test_key_not_exposed_in_status(self):
        with patch.object(cloudru,"_protect",side_effect=lambda content,encrypt:content):
            cloudru.store_key(self.data,"mock-credential")
            self.assertTrue(cloudru.has_key(self.data))
            self.assertEqual(cloudru.load_key(self.data),"mock-credential")
            cloudru.delete_key(self.data)
            self.assertFalse(cloudru.has_key(self.data))


if __name__ == "__main__":
    unittest.main()
