"""Read-only GitHub update discovery. Never installs untrusted remote code."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

API = "https://api.github.com/repos/Aspksa/Sakuya/commits/main"
SHA = re.compile(r"^[0-9a-f]{40}$")


def check_remote(timeout=6, opener=None):
    """Fetch branch commit metadata without a token; private repositories need auth."""
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Sakuya-Updater/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with opener(request, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError("Unexpected GitHub response")
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("GitHub response too large")
            result = json.loads(raw)
        sha = result.get("sha")
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError("Invalid GitHub commit SHA")
        return {"reachable": True, "commit": sha, "repository": "Aspksa/Sakuya",
                "installation_supported": False}
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 404):
            return {"reachable": False, "reason": "Репозиторий закрыт или API недоступен",
                    "repository": "Aspksa/Sakuya", "installation_supported": False}
        raise
