"""SQLite-backed local conversations, memory and limited attachment extraction.

No dynamic SQL identifiers or untrusted filesystem paths. Database is kept in
SAKUYA_DATA_DIR and excluded from code releases and GitHub.
"""
from __future__ import annotations
import base64
import os
import platform
from contextlib import contextmanager, closing
import io
import json
from pathlib import Path
import re
import sqlite3
import time
import threading
import uuid
import zipfile
import hashlib

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
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, original_name TEXT NOT NULL,
            bytes INTEGER NOT NULL, created REAL NOT NULL
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
    if extension not in (".txt",".md",".json",".csv",".log",".py",".js",".css",".html",".docx",".pdf"):
        raise ValueError("Для чтения поддерживаются TXT, MD, JSON, CSV, код и DOCX")
    if len(encoded)>((MAX_ATTACHMENT+2)//3)*4+8:
        raise ValueError("Вложение превышает 2 МиБ")
    try:
        raw=base64.b64decode(encoded,validate=True)
    except (ValueError,Exception) as error:
        raise ValueError("Невозможно прочитать вложение") from None
    if len(raw)>MAX_ATTACHMENT:
        raise ValueError("Вложение превышает 2 МиБ")
    if extension==".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("Чтение PDF требует бесплатной библиотеки pypdf в Python Sakuya") from None
        try:
            document = PdfReader(io.BytesIO(raw))
            if document.is_encrypted:
                raise ValueError("Зашифрованный PDF не поддерживается")
            text = "\\n".join((page.extract_text() or "") for page in document.pages[:100])
        except ValueError:
            raise
        except Exception:
            raise ValueError("Не удалось извлечь текст из PDF") from None
    elif extension==".docx":
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


def backup(data: Path) -> dict:
    """Online consistent SQLite snapshot; never overwrites previous backups."""
    archive = data / "backups"
    archive.mkdir(parents=True,exist_ok=True)
    name="workspace-"+time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8]+".sqlite3"
    target=archive/name
    with database(data) as source:
        with closing(sqlite3.connect(str(target))) as destination:
            source.backup(destination)
    return {"name":name,"size":target.stat().st_size}


_cpu_lock=threading.Lock()
_cpu_sample=[time.monotonic(),time.process_time()]

def system_status() -> dict:
    """Best-effort process metrics without third-party packages."""
    result={"python":platform.python_version(),"platform":platform.system(),
            "pid":os.getpid(),"cpu_count":os.cpu_count() or 1}
    with _cpu_lock:
        now_wall=time.monotonic()
        now_cpu=time.process_time()
        elapsed=max(.001,now_wall-_cpu_sample[0])
        diff=max(0,now_cpu-_cpu_sample[1])
        result["process_cpu_percent"]=round(min(100.0,100*diff/elapsed/max(1,os.cpu_count() or 1)),1)
        _cpu_sample[:]=[now_wall,now_cpu]
    if os.name=="nt":
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[("length",ctypes.c_ulong),("load",ctypes.c_ulong),
                      ("total_phys",ctypes.c_ulonglong),("avail_phys",ctypes.c_ulonglong),
                      ("total_page",ctypes.c_ulonglong),("avail_page",ctypes.c_ulonglong),
                      ("total_virtual",ctypes.c_ulonglong),("avail_virtual",ctypes.c_ulonglong),
                      ("avail_extended",ctypes.c_ulonglong)]
        state=Memory();state.length=ctypes.sizeof(Memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            result["ram_total_bytes"]=state.total_phys
            result["ram_available_bytes"]=state.avail_phys
        import ctypes.wintypes
        process=ctypes.windll.kernel32.GetCurrentProcess()
        class Counters(ctypes.Structure):
            _fields_=[("cb",ctypes.wintypes.DWORD),
                ("PageFaultCount",ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize",ctypes.c_size_t),
                ("WorkingSetSize",ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage",ctypes.c_size_t),
                ("QuotaPagedPoolUsage",ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage",ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage",ctypes.c_size_t),
                ("PagefileUsage",ctypes.c_size_t),
                ("PeakPagefileUsage",ctypes.c_size_t)]
        counters=Counters();counters.cb=ctypes.sizeof(Counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(process,ctypes.byref(counters),counters.cb):
            result["process_ram_bytes"]=counters.WorkingSetSize
    else:
        try:
            import resource
            usage=resource.getrusage(resource.RUSAGE_SELF)
            result["cpu_seconds"]=round(usage.ru_utime+usage.ru_stime,2)
        except ImportError:
            pass
        try:
            result["load_average"]=list(os.getloadavg())
        except OSError:
            pass
    return result


DOCUMENT_ID=re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,8}$")
DOCUMENT_EXT={".txt",".md",".json",".csv",".log",".py",".js",".css",".html",".docx",".pdf",".png",".jpg",".jpeg",".webp"}


def list_documents(data: Path) -> list[dict]:
    folder=data/"documents"
    folder.mkdir(parents=True,exist_ok=True)
    result=[]
    for entry in folder.iterdir():
        if entry.is_symlink() or not entry.is_file() or not DOCUMENT_ID.fullmatch(entry.name):
            continue
        stat=entry.stat()
        result.append({"id":entry.name,"bytes":stat.st_size,"modified":stat.st_mtime})
    with database(data) as db:
        names={row[0]:row[1] for row in db.execute("SELECT id,original_name FROM documents")}
    for item in result:
        item["name"]=names.get(item["id"],item["id"])
    result.sort(key=lambda item:item["modified"],reverse=True)
    return result[:500]


def save_document(data: Path, filename: str, encoded: str) -> dict:
    if not isinstance(filename,str) or not NAME.fullmatch(filename):
        raise ValueError("Недопустимое имя документа")
    extension=Path(filename).suffix.lower()
    if extension not in DOCUMENT_EXT:
        raise ValueError("Неподдерживаемый тип файла")
    if not isinstance(encoded,str) or len(encoded)>4*1024*1024:
        raise ValueError("Слишком большой файл")
    try:
        content=base64.b64decode(encoded,validate=True)
    except Exception:
        raise ValueError("Неверное кодирование файла") from None
    if not content or len(content)>2*1024*1024:
        raise ValueError("Размер документа должен быть от 1 байта до 2 МиБ")
    folder=data/"documents"
    folder.mkdir(parents=True,exist_ok=True)
    uid=uuid.uuid4().hex+extension
    path=folder/uid
    with path.open("xb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    try:
        with database(data) as db:
            db.execute("INSERT INTO documents VALUES (?,?,?,?)",
                       (uid,filename,len(content),time.time()))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id":uid,"name":filename,"bytes":len(content),
            "sha256":hashlib.sha256(content).hexdigest()}


def document_path(data: Path, uid: str) -> Path:
    if not isinstance(uid,str) or not DOCUMENT_ID.fullmatch(uid):
        raise ValueError("Неверный идентификатор документа")
    folder=(data/"documents").resolve()
    candidate=folder/uid
    if candidate.is_symlink() or not candidate.is_file() or candidate.resolve().parent!=folder:
        raise ValueError("Документ не найден")
    return candidate


def delete_document(data: Path, uid: str) -> None:
    document_path(data,uid).unlink()
    with database(data) as db:
        db.execute("DELETE FROM documents WHERE id=?", (uid,))


def document_preview(data: Path, uid: str) -> str:
    path=document_path(data,uid)
    if path.suffix.lower() in (".png",".jpg",".jpeg",".webp"):
        raise ValueError("Предпросмотр изображения здесь недоступен")
    return extract_attachment(uid,base64.b64encode(path.read_bytes()).decode("ascii"))
