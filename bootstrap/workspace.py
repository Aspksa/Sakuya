"""SQLite-backed local conversations, memory and limited attachment extraction.

No dynamic SQL identifiers or untrusted filesystem paths. Database is kept in
SAKUYA_DATA_DIR and excluded from code releases and GitHub.
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
import io
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
import zipfile

MAX_ATTACHMENT = 2 * 1024 * 1024
MAX_EXTRACTED = 200_000
NAME = re.compile(r"^[a-zA-Z0-9 _а-яА-ЯёЁ.()\-]{1,160}$")
ID = re.compile(r"^[0-9a-f]{32}$")


@contextmanager
def database(data: Path):
    path = data / "workspace.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL, created REAL NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id);
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL, created REAL NOT NULL
        );
        """)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def identifier(value: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("Недопустимый идентификатор")
    return value


def create_chat(data: Path, title: str = "Новый диалог") -> dict:
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise ValueError("Недопустимое название")
    item = {"id":uuid.uuid4().hex, "title":title.strip(), "created":time.time()}
    with database(data) as db:
        db.execute("INSERT INTO conversations VALUES (?,?,?,?)",
                   (item["id"],item["title"],item["created"],item["created"]))
    return item


def list_chats(data: Path, search: str = "") -> list[dict]:
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError("Некорректная строка поиска")
    with database(data) as db:
        if search:
            rows = db.execute("SELECT DISTINCT c.id,c.title,c.updated FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id WHERE c.title LIKE ? OR m.content LIKE ? ORDER BY c.updated DESC LIMIT 200",
                              ("%"+search+"%","%"+search+"%")).fetchall()
        else:
            rows = db.execute("SELECT id,title,updated FROM conversations ORDER BY updated DESC LIMIT 200").fetchall()
    return [dict(id=r[0], title=r[1], updated=r[2]) for r in rows]


def chat_messages(data: Path, chat_id: str) -> list[dict]:
    identifier(chat_id)
    with database(data) as db:
        if not db.execute("SELECT 1 FROM conversations WHERE id=?", (chat_id,)).fetchone():
            raise ValueError("Диалог не найден")
        rows = db.execute("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id",
                          (chat_id,)).fetchall()
    return [dict(role=r[0],content=r[1]) for r in rows]


def rename_chat(data: Path, chat_id: str, title: str) -> None:
    identifier(chat_id)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise ValueError("Название должно содержать от 1 до 120 символов")
    with database(data) as db:
        if not db.execute("UPDATE conversations SET title=?,updated=? WHERE id=?",
                          (title.strip(), time.time(),chat_id)).rowcount:
            raise ValueError("Диалог не найден")


def delete_chat(data: Path, chat_id: str) -> None:
    identifier(chat_id)
    with database(data) as db:
        db.execute("DELETE FROM conversations WHERE id=?", (chat_id,))


def append_exchange(data: Path, chat_id: str, user: str, answer: str) -> None:
    identifier(chat_id)
    if not all(isinstance(v,str) and v for v in (user,answer)):
        raise ValueError("Пустое сообщение")
    with database(data) as db:
        if not db.execute("SELECT 1 FROM conversations WHERE id=?", (chat_id,)).fetchone():
            raise ValueError("Диалог не найден")
        now = time.time()
        db.executemany("INSERT INTO messages(conversation_id,role,content,created) VALUES (?,?,?,?)",
                       [(chat_id,"user",user,now),(chat_id,"assistant",answer,now)])
        db.execute("UPDATE conversations SET updated=? WHERE id=?", (now,chat_id))


def save_memory(data: Path, content: str) -> dict:
    if not isinstance(content,str) or not 1 <= len(content.strip()) <= 10000:
        raise ValueError("Запись памяти должна содержать от 1 до 10000 символов")
    item={"id":uuid.uuid4().hex,"content":content.strip(),"created":time.time()}
    with database(data) as db:
        db.execute("INSERT INTO memories VALUES (?,?,?)",
                   (item["id"],item["content"],item["created"]))
    return item


def list_memory(data: Path) -> list[dict]:
    with database(data) as db:
        rows = db.execute("SELECT id,content,created FROM memories ORDER BY created DESC LIMIT 500").fetchall()
    return [dict(id=r[0],content=r[1],created=r[2]) for r in rows]


def delete_memory(data: Path, memory_id: str) -> None:
    identifier(memory_id)
    with database(data) as db:
        db.execute("DELETE FROM memories WHERE id=?", (memory_id,))


def extract_attachment(name: str, encoded: str) -> str:
    """Read a small text / docx attachment without writing or executing anything."""
    if not isinstance(name,str) or not NAME.fullmatch(name) or not isinstance(encoded,str):
        raise ValueError("Недопустимое вложение")
    extension=Path(name).suffix.lower()
    if extension not in (".txt",".md",".json",".csv",".log",".py",".js",".css",".html",".docx"):
        raise ValueError("Для чтения поддерживаются TXT, MD, JSON, CSV, код и DOCX")
    if len(encoded)>((MAX_ATTACHMENT+2)//3)*4+8:
        raise ValueError("Вложение превышает 2 МиБ")
    try:
        raw=base64.b64decode(encoded,validate=True)
    except (ValueError,Exception) as error:
        raise ValueError("Невозможно прочитать вложение") from None
    if len(raw)>MAX_ATTACHMENT:
        raise ValueError("Вложение превышает 2 МиБ")
    if extension==".docx":
        from xml.etree import ElementTree as ET
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("DOCX не содержит текста")
                entry=archive.getinfo("word/document.xml")
                if entry.file_size>MAX_EXTRACTED*8:
                    raise ValueError("Слишком большой текст DOCX")
                document=archive.read(entry)
            root=ET.fromstring(document)
            text=" ".join(element.text or "" for element in root.iter()
                          if element.tag.endswith("}t"))
        except (zipfile.BadZipFile,ET.ParseError):
            raise ValueError("Повреждённый DOCX") from None
    else:
        text=raw.decode("utf-8-sig",errors="replace")
    if len(text)>MAX_EXTRACTED:
        text=text[:MAX_EXTRACTED]+"\n[Текст вложения сокращён по техническому пределу]"
    return "Вложение: "+name+"\n"+text


def status(data: Path) -> dict:
    with database(data) as db:
        chats=db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        messages=db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        memories=db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    return {"chats":chats,"messages":messages,"memories":memories,
            "database_bytes":(data/"workspace.sqlite3").stat().st_size}
