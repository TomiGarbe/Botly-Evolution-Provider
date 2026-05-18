from __future__ import annotations

import base64
import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_LOCK = threading.Lock()

AuthType = Literal["NONE", "BEARER", "API_KEY", "BASIC", "CUSTOM_HEADERS"]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _storage_path() -> Path:
    settings = get_settings()
    return Path(settings.instance_webhooks_path).resolve()


def _empty_store() -> dict[str, Any]:
    return {"instances": {}}


def _read_store_unlocked() -> dict[str, Any]:
    path = _storage_path()
    if not path.exists():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("instance_webhooks_store_read_failed", error=str(exc))
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    instances = raw.get("instances")
    if not isinstance(instances, dict):
        return _empty_store()
    return {"instances": instances}


def _write_store_unlocked(store: dict[str, Any]) -> None:
    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=True, indent=2), encoding="utf-8")


def _sanitize_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        k = str(key or "").strip()
        if not k:
            continue
        out[k] = str(value or "").strip()
    return out


def _sanitize_auth_config(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    allowed = ("token", "headerName", "apiKey", "username", "password")
    for key in allowed:
        value = str(raw.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _sanitize_webhook(instance_name: str, record: dict[str, Any]) -> dict[str, Any]:
    auth_type = str(record.get("authType") or "NONE").upper()
    if auth_type not in {"NONE", "BEARER", "API_KEY", "BASIC", "CUSTOM_HEADERS"}:
        auth_type = "NONE"

    return {
        "id": str(record.get("id") or str(uuid.uuid4())[:16]),
        "instanceId": instance_name,
        "url": str(record.get("url") or "").strip(),
        "enabled": bool(record.get("enabled", True)),
        "authType": auth_type,
        "authConfig": _sanitize_auth_config(record.get("authConfig")),
        "customHeaders": _sanitize_headers(record.get("customHeaders")),
        "createdAt": str(record.get("createdAt") or _now_iso()),
        "updatedAt": str(record.get("updatedAt") or _now_iso()),
        "lastUsedAt": record.get("lastUsedAt"),
        "lastStatus": record.get("lastStatus"),
        "lastError": record.get("lastError"),
    }


def _mask_secret(value: str, keep_start: int = 4, keep_end: int = 2) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= keep_start + keep_end:
        return "*" * len(raw)
    return f"{raw[:keep_start]}...{raw[-keep_end:]}"


def _public_webhook(record: dict[str, Any], reveal_secrets: bool = False) -> dict[str, Any]:
    item = copy.deepcopy(record)
    auth = item.get("authConfig") if isinstance(item.get("authConfig"), dict) else {}
    safe_auth: dict[str, Any] = {}

    for key, value in auth.items():
        if key in {"token", "apiKey", "password"} and not reveal_secrets:
            safe_auth[key] = _mask_secret(str(value))
            safe_auth[f"{key}Masked"] = _mask_secret(str(value))
        else:
            safe_auth[key] = value

    item["authConfig"] = safe_auth
    return item


def list_instance_webhooks(instance_name: str, reveal_secrets: bool = False) -> list[dict[str, Any]]:
    with _LOCK:
        store = _read_store_unlocked()
        raw_list = store["instances"].get(instance_name) or []
        if not isinstance(raw_list, list):
            return []
        clean = [_sanitize_webhook(instance_name, item) for item in raw_list if isinstance(item, dict)]
        store["instances"][instance_name] = clean
        _write_store_unlocked(store)
    return [_public_webhook(item, reveal_secrets=reveal_secrets) for item in clean]


def get_webhook(instance_name: str, webhook_id: str, reveal_secrets: bool = False) -> dict[str, Any] | None:
    hooks = list_instance_webhooks(instance_name, reveal_secrets=True)
    for hook in hooks:
        if hook["id"] == webhook_id:
            return _public_webhook(hook, reveal_secrets=reveal_secrets)
    return None


def create_webhook(
    instance_name: str,
    *,
    url: str,
    enabled: bool,
    auth_type: AuthType,
    auth_config: dict[str, Any] | None,
    custom_headers: dict[str, Any] | None,
) -> dict[str, Any]:
    now = _now_iso()
    new_item = _sanitize_webhook(
        instance_name,
        {
            "id": str(uuid.uuid4())[:16],
            "instanceId": instance_name,
            "url": url,
            "enabled": enabled,
            "authType": auth_type,
            "authConfig": auth_config or {},
            "customHeaders": custom_headers or {},
            "createdAt": now,
            "updatedAt": now,
            "lastUsedAt": None,
            "lastStatus": None,
            "lastError": None,
        },
    )
    with _LOCK:
        store = _read_store_unlocked()
        hooks = store["instances"].get(instance_name)
        if not isinstance(hooks, list):
            hooks = []
        hooks.append(new_item)
        store["instances"][instance_name] = hooks
        _write_store_unlocked(store)
    return _public_webhook(new_item)


def update_webhook(
    instance_name: str,
    webhook_id: str,
    *,
    url: str,
    enabled: bool,
    auth_type: AuthType,
    auth_config: dict[str, Any] | None,
    custom_headers: dict[str, Any] | None,
) -> dict[str, Any] | None:
    with _LOCK:
        store = _read_store_unlocked()
        hooks = store["instances"].get(instance_name)
        if not isinstance(hooks, list):
            return None
        for idx, item in enumerate(hooks):
            if not isinstance(item, dict) or str(item.get("id")) != webhook_id:
                continue
            merged = _sanitize_webhook(
                instance_name,
                {
                    **item,
                    "url": url,
                    "enabled": enabled,
                    "authType": auth_type,
                    "authConfig": auth_config or {},
                    "customHeaders": custom_headers or {},
                    "updatedAt": _now_iso(),
                },
            )
            hooks[idx] = merged
            store["instances"][instance_name] = hooks
            _write_store_unlocked(store)
            return _public_webhook(merged)
    return None


def set_webhook_enabled(instance_name: str, webhook_id: str, enabled: bool) -> dict[str, Any] | None:
    with _LOCK:
        store = _read_store_unlocked()
        hooks = store["instances"].get(instance_name)
        if not isinstance(hooks, list):
            return None
        for idx, item in enumerate(hooks):
            if not isinstance(item, dict) or str(item.get("id")) != webhook_id:
                continue
            merged = _sanitize_webhook(instance_name, {**item, "enabled": enabled, "updatedAt": _now_iso()})
            hooks[idx] = merged
            store["instances"][instance_name] = hooks
            _write_store_unlocked(store)
            return _public_webhook(merged)
    return None


def delete_webhook(instance_name: str, webhook_id: str) -> bool:
    with _LOCK:
        store = _read_store_unlocked()
        hooks = store["instances"].get(instance_name)
        if not isinstance(hooks, list):
            return False
        next_hooks = [item for item in hooks if isinstance(item, dict) and str(item.get("id")) != webhook_id]
        if len(next_hooks) == len(hooks):
            return False
        store["instances"][instance_name] = next_hooks
        _write_store_unlocked(store)
    return True


def delete_all_instance_webhooks(instance_name: str) -> None:
    with _LOCK:
        store = _read_store_unlocked()
        if instance_name in store["instances"]:
            del store["instances"][instance_name]
            _write_store_unlocked(store)


def list_enabled_webhooks_for_dispatch(instance_name: str) -> list[dict[str, Any]]:
    hooks = list_instance_webhooks(instance_name, reveal_secrets=True)
    return [item for item in hooks if item.get("enabled") and str(item.get("url") or "").startswith(("http://", "https://"))]


def build_auth_headers(item: dict[str, Any]) -> dict[str, str]:
    auth_type = str(item.get("authType") or "NONE").upper()
    auth = item.get("authConfig") if isinstance(item.get("authConfig"), dict) else {}
    headers: dict[str, str] = {}

    if auth_type == "BEARER":
        token = str(auth.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "API_KEY":
        header_name = str(auth.get("headerName") or "x-api-key").strip()
        api_key = str(auth.get("apiKey") or "").strip()
        if header_name and api_key:
            headers[header_name] = api_key
    elif auth_type == "BASIC":
        username = str(auth.get("username") or "")
        password = str(auth.get("password") or "")
        if username or password:
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

    custom_headers = item.get("customHeaders") if isinstance(item.get("customHeaders"), dict) else {}
    for key, value in custom_headers.items():
        k = str(key or "").strip()
        if not k:
            continue
        if k.lower() == "content-type":
            continue
        headers[k] = str(value or "").strip()

    return headers


def mark_dispatch_result(instance_name: str, webhook_id: str, status: str, error: str | None = None) -> None:
    with _LOCK:
        store = _read_store_unlocked()
        hooks = store["instances"].get(instance_name)
        if not isinstance(hooks, list):
            return
        for idx, item in enumerate(hooks):
            if not isinstance(item, dict) or str(item.get("id")) != webhook_id:
                continue
            merged = _sanitize_webhook(
                instance_name,
                {
                    **item,
                    "lastUsedAt": _now_iso(),
                    "lastStatus": status,
                    "lastError": (error or "")[:300] if error else None,
                    "updatedAt": _now_iso(),
                },
            )
            hooks[idx] = merged
            store["instances"][instance_name] = hooks
            _write_store_unlocked(store)
            return


def mask_headers_for_log(headers: dict[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in {"authorization", "x-api-key", "api-key"} or "token" in lower or "secret" in lower:
            safe[key] = _mask_secret(value)
        else:
            safe[key] = value
    return safe
