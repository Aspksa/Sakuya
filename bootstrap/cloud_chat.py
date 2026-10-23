"""Minimal bounded Cloud.ru chat completion adapter; credentials remain server-side."""
import json
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
