"""Minimal bounded Cloud.ru chat completion adapter; credentials remain server-side."""
import json
import urllib.error
import urllib.request
from cloudru import BASE, MODEL_RE, get_config, load_key

MAX_MESSAGE = 4000
MAX_HISTORY = 12000
MAX_ITEMS = 20

def compact_messages(messages):
    """Preserve recent dialog within local limits."""
    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
        raise ValueError("Недопустимая история диалога")
    checked = []
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"} or item["role"] not in ("user", "assistant"):
            raise ValueError("Недопустимое сообщение")
        value = item["content"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Пустое сообщение")
        if len(value) > MAX_MESSAGE:
            if item["role"] == "user":
                raise ValueError("Сообщение превышает 4000 символов")
            value = value[:MAX_MESSAGE]
        checked.append({"role": item["role"], "content": value})
    if checked[-1]["role"] != "user":
        raise ValueError("Последнее сообщение должно быть от пользователя")
    result, size = [], 0
    for item in reversed(checked):
        length = len(item["content"])
        if len(result) >= MAX_ITEMS or size + length > MAX_HISTORY:
            break
        result.append(item)
        size += length
    return list(reversed(result))


def complete(data, messages, opener=None):
    model = get_config(data)["model"]
    if not model or not MODEL_RE.fullmatch(model):
        raise ValueError("Выберите модель Cloud.ru в настройках")
    validated = compact_messages(messages)
    payload = json.dumps({"model":model, "messages":validated, "stream":False,
                          "max_completion_tokens":1024},ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + "/chat/completions", data=payload, method="POST",
                                     headers={"Content-Type":"application/json","Accept":"application/json",
                                              "Authorization":"Bearer " + load_key(data)})
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=45) as response:
            if response.status != 200:
                raise ValueError("Неожиданный ответ Cloud.ru")
            raw = response.read(262145)
            if len(raw) > 262144:
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
    return {"answer":answer[:16000], "model":model, "truncated":len(answer) > 16000}
