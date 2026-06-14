"""Local state: config, credentials and the CAS session cookie.

Everything lives under ``~/.qzcli/``:
  - ``config.json``       — api_base_url, proxy
  - ``credentials.json``  — username/password, used for 401 auto-relogin
  - ``.cookie``           — {"cookie": "...", "workspace_id": "...", ...}

Credentials and cookie are written with 0600 permissions. Storing the password
locally is what makes unattended 401 auto-relogin possible; set it via env vars
instead (``QZCLI_USERNAME`` / ``QZCLI_PASSWORD``) if you'd rather not persist it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE_URL = "https://qz.sii.edu.cn"

CONFIG_DIR = Path.home() / ".qzcli"
CONFIG_FILE = CONFIG_DIR / "config.json"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
COOKIE_FILE = CONFIG_DIR / ".cookie"


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path: Path, data: dict[str, Any], *, private: bool = False) -> None:
    ensure_config_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if private:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# --- config.json ---------------------------------------------------------

def load_config() -> dict[str, Any]:
    return _load_json(CONFIG_FILE)


def save_config(config: dict[str, Any]) -> None:
    _save_json(CONFIG_FILE, config)


def get_api_base_url() -> str:
    return (
        os.environ.get("QZCLI_BASE_URL")
        or load_config().get("api_base_url")
        or DEFAULT_BASE_URL
    ).rstrip("/")


def get_proxy() -> str:
    """Resolve the proxy URL.

    Precedence: ``~/.qzcli/config.json`` ``proxy`` field → ``all_proxy`` /
    ``https_proxy`` / ``http_proxy`` env vars. Empty string means "no proxy".
    """
    cfg = load_config().get("proxy")
    if cfg:
        return str(cfg).strip()
    for var in ("all_proxy", "ALL_PROXY", "https_proxy", "HTTPS_PROXY",
                "http_proxy", "HTTP_PROXY"):
        val = os.environ.get(var)
        if val:
            return val.strip()
    return ""


# --- credentials.json ----------------------------------------------------

def get_credentials() -> tuple[Optional[str], Optional[str]]:
    """Return (username, password) from env vars first, then credentials.json."""
    user = os.environ.get("QZCLI_USERNAME")
    pwd = os.environ.get("QZCLI_PASSWORD")
    if user and pwd:
        return user, pwd
    data = _load_json(CREDENTIALS_FILE)
    return (
        user or data.get("username"),
        pwd or data.get("password"),
    )


def save_credentials(username: str, password: str) -> None:
    _save_json(
        CREDENTIALS_FILE,
        {"username": username, "password": password},
        private=True,
    )


# --- .cookie -------------------------------------------------------------

def save_cookie(cookie: str, workspace_id: str = "") -> None:
    _save_json(
        COOKIE_FILE,
        {"cookie": cookie, "workspace_id": workspace_id},
        private=True,
    )


def get_cookie() -> Optional[dict[str, Any]]:
    data = _load_json(COOKIE_FILE)
    return data or None


def clear_cookie() -> None:
    try:
        COOKIE_FILE.unlink()
    except FileNotFoundError:
        pass
