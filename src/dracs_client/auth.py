"""Token storage and authentication helpers for dracs-client."""

import json
import os
import stat
from pathlib import Path
from typing import Optional

TOKEN_DIR = Path.home() / ".config" / "dracs"
TOKEN_PATH = TOKEN_DIR / "login_token"


def save_token(token: str, role: str, server: str) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    data = {"token": token, "role": role, "server": server}
    TOKEN_PATH.write_text(json.dumps(data))
    os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)


def load_token(server: str) -> Optional[dict]:
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
        if data.get("server") != server:
            return None
        return data
    except (json.JSONDecodeError, KeyError):
        return None


def clear_token() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def auth_headers(server: str) -> dict:
    data = load_token(server)
    if data and data.get("token"):
        return {"Authorization": f"Bearer {data['token']}"}
    return {}


def get_current_role(server: str) -> Optional[str]:
    data = load_token(server)
    if data:
        return data.get("role")
    return None
