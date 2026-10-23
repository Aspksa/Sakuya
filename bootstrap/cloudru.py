"""Local Cloud.ru Foundation Models configuration and private credential storage."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from common import atomic_json

BASE = "https://foundation-models.api.cloud.ru/v1"
MODEL_RE = re.compile(r"^[A-Za-z0-9_./:-]{1,160}$")


def _protect(secret: bytes, encrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Secure credential storage requires Windows")
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source_buffer = ctypes.create_string_buffer(secret)
    source = BLOB(len(secret), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    target = BLOB()
    crypt = ctypes.windll.crypt32
    func = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    func.argtypes = ([ctypes.POINTER(BLOB), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)] if encrypt else
                     [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)])
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def credential_path(data: Path) -> Path:
    return data / "secrets" / "cloudru.dpapi"


def has_key(data: Path) -> bool:
    return credential_path(data).is_file()


def store_key(data: Path, key: str) -> None:
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(ord(x) < 32 for x in key):
        raise ValueError("Некорректный API-ключ")
    protected = _protect(key.encode("utf-8"), True)
    path = credential_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, temp = tempfile.mkstemp(prefix=".cloudru-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(protected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def load_key(data: Path) -> str:
    return _protect(credential_path(data).read_bytes(), False).decode("utf-8")


def delete_key(data: Path) -> None:
    credential_path(data).unlink(missing_ok=True)


def config_path(data: Path) -> Path:
    return data / "cloudru.json"


def get_config(data: Path) -> dict:
    if not config_path(data).is_file():
        return {"model": ""}
    value = json.loads(config_path(data).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("model"), str):
        raise ValueError("Повреждены настройки Cloud.ru")
    return {"model": value["model"]}


def set_model(data: Path, model: str) -> None:
    if not isinstance(model, str) or (model and not MODEL_RE.fullmatch(model)):
        raise ValueError("Недопустимое имя модели")
    atomic_json(config_path(data), {"model": model})


def fetch_models(data: Path, opener=None) -> list[str]:
    key = load_key(data)
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(BASE + "/models", headers={
        "Authorization": "Bearer " + key, "Accept": "application/json",
        "User-Agent": "Sakuya/CloudRU",
    })
    try:
        with opener(request, timeout=12) as response:
            if response.status != 200:
                raise ValueError("Сервис вернул некорректный статус")
            raw = response.read(131073)
            if len(raw) > 131072:
                raise ValueError("Ответ Cloud.ru слишком большой")
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError("Ключ не принят Cloud.ru") from None
        if error.code == 429:
            raise ValueError("Превышен лимит запросов Cloud.ru") from None
        raise ValueError("Cloud.ru недоступен (HTTP " + str(error.code) + ")") from None
    result = json.loads(raw)
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise ValueError("Некорректный ответ моделей Cloud.ru")
    return sorted({row["id"] for row in result["data"]
                   if isinstance(row, dict) and isinstance(row.get("id"), str)
                   and MODEL_RE.fullmatch(row["id"])})[:300]
