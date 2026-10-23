"""Workspace persistence, text attachments, and streaming integrations."""
from pathlib import Path
import base64
import io
import json
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"bootstrap"))
import workspace
import cloud_chat

class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.data=Path(self.folder.name)
    def tearDown(self):
        self.folder.cleanup()

    def test_persisted_chats_rename_search_delete(self):
        item=workspace.create_chat(self.data,"Разработка")
        workspace.append_exchange(self.data,item["id"],"Вопрос Sakuya","Ответ Cloud.ru")
        self.assertEqual(len(workspace.chat_messages(self.data,item["id"])),2)
        self.assertEqual(len(workspace.list_chats(self.data,search="Вопрос")),1)
        workspace.rename_chat(self.data,item["id"],"Проект Sakuya")
        self.assertEqual(workspace.list_chats(self.data)[0]["title"],"Проект Sakuya")
        # A new database connection sees persisted data.
        self.assertEqual(workspace.status(self.data)["messages"],2)
        workspace.delete_chat(self.data,item["id"])
        self.assertEqual(workspace.status(self.data)["messages"],0)

    def test_memory_lifecycle(self):
        item=workspace.save_memory(self.data,"Память Sakuya")
        self.assertEqual(workspace.list_memory(self.data)[0]["content"],"Память Sakuya")
        workspace.delete_memory(self.data,item["id"])
        self.assertEqual(workspace.list_memory(self.data),[])

    def test_attachment_plain_text(self):
        content=base64.b64encode("Привет мир".encode()).decode()
        self.assertIn("Привет мир",workspace.extract_attachment("тест.txt",content))
        with self.assertRaises(ValueError):
            workspace.extract_attachment("../bad.exe",content)

    def test_docx_extraction(self):
        memory=io.BytesIO()
        with zipfile.ZipFile(memory,"w") as file:
            file.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Текст документа</w:t></w:r></w:p></w:body></w:document>')
        result=workspace.extract_attachment("отчёт.docx",base64.b64encode(memory.getvalue()).decode())
        self.assertIn("Текст документа",result)

    def test_invalid_identifiers(self):
        with self.assertRaises(ValueError):
            workspace.chat_messages(self.data,"../outside")

    def test_stream_parses_deltas(self):
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __iter__(self):
                return iter(['data: {"choices":[{"delta":{"content":"Привет"}}]}\n'.encode("utf-8"),
                             b'data: {"choices":[{"delta":{"content":"!"}}]}\n',
                             b'data: [DONE]\n'])
        def opener(request,timeout):
            self.assertEqual(timeout,300)
            self.assertTrue(json.loads(request.data)["stream"])
            return Stream()
        with patch.object(cloud_chat,"load_key",return_value="secret"):
            result=list(cloud_chat.stream(self.data,[{"role":"user","content":"Hello"}],opener=opener,model="test/model"))
        self.assertEqual(result,["Привет","!"])

if __name__=="__main__":
    unittest.main()
