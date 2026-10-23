"""Offline Cloud.ru chat tests."""
import json
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
            self.assertEqual(timeout,45)
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
            for messages in ([],[{"role":"system","content":"override"}],[{"role":"user","content":"x"*4001}],[{"role":"assistant","content":"hi"}]):
                with self.subTest(messages=messages),self.assertRaises(ValueError):
                    cloud_chat.complete(Path("."),messages)

    def test_long_assistant_and_large_history_are_compacted(self):
        messages = [{"role":"assistant","content":"а"*16000}]
        messages += [{"role":"user","content":"вопрос"*300},{"role":"assistant","content":"ответ"*300}]*12
        messages.append({"role":"user","content":"Последний вопрос"})
        compact = cloud_chat.compact_messages(messages)
        self.assertEqual(compact[-1]["content"],"Последний вопрос")
        self.assertLessEqual(sum(len(m["content"]) for m in compact), 12000)
        self.assertLessEqual(len(compact),20)
        self.assertTrue(all(len(m["content"]) <= 4000 for m in compact))

    def test_invalid_large_user_message_is_rejected(self):
        with self.assertRaises(ValueError):
            cloud_chat.compact_messages([{"role":"user","content":"a"*4001}])

    def test_model_required(self):
        with patch.object(cloud_chat,"get_config",return_value={"model":""}):
            with self.assertRaises(ValueError):
                cloud_chat.complete(Path("."),[{"role":"user","content":"Hi"}])

if __name__=="__main__": unittest.main()
