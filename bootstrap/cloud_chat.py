"""Minimal bounded Cloud.ru chat completion adapter; credentials remain server-side."""
import json
import base64
import urllib.error
import urllib.request
from cloudru import BASE, MODEL_RE, get_config, load_key

# Protocol safeguards only; model context limits are enforced by Cloud.ru.
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

def compact_messages(messages):
    """Validate without discarding messages or silently truncating content."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("История диалога должна содержать сообщения")
    checked = []
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"} or item["role"] not in ("user", "assistant"):
            raise ValueError("Недопустимое сообщение")
        value = item["content"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Пустое сообщение")
        checked.append({"role":item["role"], "content":value})
    if checked[-1]["role"] != "user":
        raise ValueError("Последнее сообщение должно быть от пользователя")
    return checked


def complete(data, messages, opener=None):
    model = get_config(data)["model"]
    if not model or not MODEL_RE.fullmatch(model):
        raise ValueError("Выберите модель Cloud.ru в настройках")
    validated = compact_messages(messages)
    payload = json.dumps({"model":model, "messages":validated, "stream":False},ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("Размер запроса превышает технический предел 8 МиБ")
    request = urllib.request.Request(BASE + "/chat/completions", data=payload, method="POST",
                                     headers={"Content-Type":"application/json","Accept":"application/json",
                                              "Authorization":"Bearer " + load_key(data)})
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=300) as response:
            if response.status != 200:
                raise ValueError("Неожиданный ответ Cloud.ru")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Ответ Cloud.ru слишком большой")
    except urllib.error.HTTPError as error:
        if error.code in (401,403):
            raise ValueError("Cloud.ru не принял API-ключ") from None
        if error.code == 429:
            raise ValueError("Превышен лимит Cloud.ru") from None
        raise ValueError("Ошибка Cloud.ru (HTTP " + str(error.code) + ")") from None
    except urllib.error.URLError:
        raise ValueError("Нет соединения с Cloud.ru") from None
    result = json.loads(raw)
    choices = result.get("choices") if isinstance(result,dict) else None
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
        raise ValueError("Cloud.ru вернул некорректный ответ")
    msg = choices[0].get("message")
    answer = msg.get("content") if isinstance(msg,dict) else None
    if not isinstance(answer,str) or not answer.strip():
        raise ValueError("Модель не вернула текст")
    return {"answer":answer, "model":model, "truncated":False}


def provider_error(error):
    """Return a small sanitized provider error; never include request headers or keys."""
    code = error.code
    if code in (401, 403):
        return "Cloud.ru не принял API-ключ (HTTP " + str(code) + ")"
    if code == 429:
        return "Cloud.ru ограничил частоту запросов (HTTP 429)"
    try:
        raw = error.read(4096)
        body = json.loads(raw)
    except (ValueError, OSError, UnicodeDecodeError):
        body = {}
    detail = body.get("error", {}) if isinstance(body, dict) else {}
    if isinstance(detail, dict):
        detail = detail.get("message", "")
    if not isinstance(detail, str):
        detail = ""
    import re
    detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer [скрыто]", detail)
    detail = re.sub(r"(?i)(api[_ -]?key|authorization|token)\s*[:=]\s*[^\s,;]+", r"\1=[скрыто]", detail)
    detail = ''.join(ch for ch in detail if ch.isprintable())[:350]
    return "Cloud.ru HTTP " + str(code) + (": " + detail if detail else
        ". Проверьте выбор модели и допустимость параметров запроса")


def stream(data, messages, opener=None, model=None, system_context=None, image=None):
    """Yield OpenAI-compatible SSE text deltas; consumer controls cancellation."""
    selected = model or get_config(data)["model"]
    if not isinstance(selected, str) or not MODEL_RE.fullmatch(selected):
        raise ValueError("Выберите модель Cloud.ru")
    history=compact_messages(messages)
    if image is not None:
        if not isinstance(image,dict) or set(image)!={"mime","base64"}:
            raise ValueError("Недопустимое изображение")
        mime=image.get("mime")
        if mime not in ("image/png","image/jpeg","image/webp"):
            raise ValueError("Поддерживаются PNG, JPEG и WebP")
        encoded=image.get("base64")
        if not isinstance(encoded,str) or len(encoded)>3*1024*1024:
            raise ValueError("Изображение слишком большое")
        try:
            raw=base64.b64decode(encoded,validate=True)
        except Exception:
            raise ValueError("Ошибка кодирования изображения") from None
        if not raw or len(raw)>2*1024*1024:
            raise ValueError("Изображение превышает 2 МиБ")
        valid={"image/png":raw.startswith(bytes.fromhex("89504e470d0a1a0a")),
               "image/jpeg":raw.startswith(bytes.fromhex("ffd8ff")),
               "image/webp":raw[:4]==b"RIFF" and raw[8:12]==b"WEBP"}
        if not valid[mime]:
            raise ValueError("Содержимое изображения не соответствует формату")
        history[-1]={"role":"user","content":[
            {"type":"text","text":history[-1]["content"]},
            {"type":"image_url","image_url":{"url":"data:"+mime+";base64,"+encoded}}
        ]}
    if system_context:
        if not isinstance(system_context,str) or len(system_context)>32000:
            raise ValueError("Память превышает лимит контекста")
        history=[{"role":"system","content":system_context}]+history
    body = json.dumps({"model": selected, "messages": history,
                       "stream": True}, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("Размер запроса превышает технический предел")
    request = urllib.request.Request(BASE+"/chat/completions",data=body,method="POST",
        headers={"Content-Type":"application/json","Accept":"text/event-stream",
                 "Authorization":"Bearer "+load_key(data)})
    opener = opener or urllib.request.urlopen
    try:
        with opener(request,timeout=300) as response:
            read_bytes=0
            for raw in response:
                read_bytes+=len(raw)
                if read_bytes>MAX_RESPONSE_BYTES:
                    raise ValueError("Ответ Cloud.ru превышает 8 МиБ")
                line=raw.decode("utf-8",errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                event=line[5:].strip()
                if event=="[DONE]":
                    return
                try:
                    item=json.loads(event)
                except json.JSONDecodeError:
                    continue
                if isinstance(item,dict) and item.get("error"):
                    raise ValueError("Ошибка потокового ответа Cloud.ru")
                choices=item.get("choices",[]) if isinstance(item,dict) else []
                if choices and isinstance(choices[0],dict):
                    delta=choices[0].get("delta",{})
                    content=delta.get("content") if isinstance(delta,dict) else None
                    if isinstance(content,str) and content:
                        yield content
    except urllib.error.HTTPError as error:
        raise ValueError(provider_error(error)) from None
    except urllib.error.URLError:
        raise ValueError("Связь с Cloud.ru прервана") from None
