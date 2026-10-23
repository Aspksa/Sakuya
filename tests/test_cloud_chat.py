"""Offline Cloud.ru chat tests."""
import json
import io
import urllib.error
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"bootstrap"))
import cloud_chat

class Response:
    status=200
    def __init__(self,value): self.value=value
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,n): return self.value[:n]

class ChatTests(unittest.TestCase):
    def test_completion_uses_selected_model_and_server_secret(self):
        def opener(request,timeout):
            self.assertEqual(timeout,300)
            self.assertEqual(request.full_url,cloud_chat.BASE+"/chat/completions")
            self.assertEqual(request.get_header("Authorization"),"Bearer hidden-key")
            body=json.loads(request.data)
            self.assertEqual(body["model"],"sample/model")
            self.assertEqual(body["messages"],[{"role":"user","content":"Привет"}])
            self.assertFalse(body["stream"])
            return Response(json.dumps({"choices":[{"message":{"content":"Здравствуйте!"}}]}).encode())
        with patch.object(cloud_chat,"get_config",return_value={"model":"sample/model"}),patch.object(cloud_chat,"load_key",return_value="hidden-key"):
            result=cloud_chat.complete(Path("."),[{"role":"user","content":"Привет"}],opener=opener)
        self.assertEqual(result["answer"],"Здравствуйте!")
        self.assertNotIn("key",result)

    def test_invalid_context_is_rejected_before_network(self):
        with patch.object(cloud_chat,"get_config",return_value={"model":"sample/model"}):
            for messages in ([],[{"role":"system","content":"override"}],[{"role":"assistant","content":"hi"}]):
                with self.subTest(messages=messages),self.assertRaises(ValueError):
                    cloud_chat.complete(Path("."),messages)

    def test_long_messages_and_history_are_preserved(self):
        messages = [{"role":"assistant","content":"а"*16000}]
        messages += [{"role":"user","content":"вопрос"*300},{"role":"assistant","content":"ответ"*300}]*12
        messages.append({"role":"user","content":"Последний вопрос"})
        self.assertEqual(cloud_chat.compact_messages(messages), messages)

    def test_user_message_longer_than_old_limit(self):
        message = {"role":"user","content":"a"*100000}
        self.assertEqual(cloud_chat.compact_messages([message]), [message])

    def test_http400_reason_is_safely_extracted(self):
        response=urllib.error.HTTPError("https://example.invalid",400,"Bad Request",{},
            io.BytesIO(json.dumps({"error":{"message":"Model not supported for streaming"}}).encode()))
        self.assertIn("Model not supported", cloud_chat.provider_error(response))

    def test_secret_is_redacted_from_provider_error(self):
        response=urllib.error.HTTPError("https://example.invalid",400,"Bad Request",{},
            io.BytesIO(b'{"error":{"message":"Authorization: secret-abcdef Bearer secret-bearer"}}'))
        message=cloud_chat.provider_error(response)
        self.assertNotIn("secret-abcdef", message)
        self.assertNotIn("secret-bearer", message)

    def test_model_required(self):
        with patch.object(cloud_chat,"get_config",return_value={"model":""}):
            with self.assertRaises(ValueError):
                cloud_chat.complete(Path("."),[{"role":"user","content":"Hi"}])

if __name__=="__main__": unittest.main()
