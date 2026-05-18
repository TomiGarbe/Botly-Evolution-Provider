from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _storage_path() -> Path:
    settings = get_settings()
    return Path(settings.instance_api_keys_path).resolve()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _masked(token: str) -> str:
    if len(token) <= 10:
        return "*" * len(token)
    return f"{token[:7]}...{token[-4:]}"


def _empty_store() -> dict[str, Any]:
    return {"instances": {}}


def _read_store_unlocked() -> dict[str, Any]:
    path = _storage_path()
    if not path.exists():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("instance_auth_store_read_failed", error=str(exc))
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


def _new_token() -> str:
    return f"inst_{secrets.token_urlsafe(32)}"


def _new_record(instance_name: str, instance_id: str | None = None) -> tuple[dict[str, Any], str]:
    token = _new_token()
    now = _now_iso()
    return (
        {
            "instanceId": instance_id or instance_name,
            "createdAt": now,
            "lastUsedAt": None,
            "enabled": True,
            "apiKeyHash": _hash_token(token),
            "apiKeyPrefix": token[:12],
        },
        token,
    )


def ensure_instance_key(instance_name: str, instance_id: str | None = None) -> dict[str, Any]:
    with _LOCK:
        store = _read_store_unlocked()
        instances = store["instances"]
        record = instances.get(instance_name)
        if isinstance(record, dict) and record.get("apiKeyHash"):
            if instance_id:
                record["instanceId"] = instance_id
                _write_store_unlocked(store)
            return {"created": False, "instance": instance_name}

        new_record, _ = _new_record(instance_name, instance_id=instance_id)
        instances[instance_name] = new_record
        _write_store_unlocked(store)
        logger.info("instance_api_key_auto_created", instance=instance_name)
        return {"created": True, "instance": instance_name}


def create_or_regenerate_instance_key(instance_name: str, instance_id: str | None = None) -> dict[str, Any]:
    with _LOCK:
        store = _read_store_unlocked()
        token_record, token = _new_record(instance_name, instance_id=instance_id)
        store["instances"][instance_name] = token_record
        _write_store_unlocked(store)
    logger.info("instance_api_key_regenerated", instance=instance_name)
    return get_instance_key_info(instance_name, reveal=False) | {"apiKey": token}


def get_instance_key_info(instance_name: str, reveal: bool = False) -> dict[str, Any]:
    with _LOCK:
        store = _read_store_unlocked()
        record = store["instances"].get(instance_name)
    if not isinstance(record, dict):
        return {
            "instanceId": instance_name,
            "createdAt": None,
            "lastUsedAt": None,
            "enabled": False,
            "hasApiKey": False,
            "maskedApiKey": None,
        }
    payload = {
        "instanceId": str(record.get("instanceId") or instance_name),
        "createdAt": record.get("createdAt"),
        "lastUsedAt": record.get("lastUsedAt"),
        "enabled": bool(record.get("enabled", False)),
        "hasApiKey": bool(record.get("apiKeyHash")),
        "maskedApiKey": None,
    }
    prefix = str(record.get("apiKeyPrefix") or "").strip()
    if prefix:
        payload["maskedApiKey"] = f"{prefix}...****"
    if reveal:
        payload["apiKey"] = None
    return payload


def revoke_instance_key(instance_name: str) -> dict[str, Any]:
    with _LOCK:
        store = _read_store_unlocked()
        record = store["instances"].get(instance_name)
        if not isinstance(record, dict):
            store["instances"][instance_name] = {
                "instanceId": instance_name,
                "createdAt": _now_iso(),
                "lastUsedAt": None,
                "enabled": False,
                "apiKeyHash": None,
                "apiKeyPrefix": None,
            }
        else:
            record["enabled"] = False
            record["apiKeyHash"] = None
            record["apiKeyPrefix"] = None
        _write_store_unlocked(store)
    logger.warning("instance_api_key_revoked", instance=instance_name)
    return get_instance_key_info(instance_name, reveal=False)


def delete_instance_key_record(instance_name: str) -> None:
    with _LOCK:
        store = _read_store_unlocked()
        if instance_name in store["instances"]:
            del store["instances"][instance_name]
            _write_store_unlocked(store)
            logger.info("instance_api_key_deleted_with_instance", instance=instance_name)


def authenticate_instance_token(token: str) -> dict[str, Any] | None:
    token_clean = str(token or "").strip()
    if not token_clean:
        return None
    token_hash = _hash_token(token_clean)
    now = _now_iso()
    with _LOCK:
        store = _read_store_unlocked()
        for instance_name, record in store["instances"].items():
            if not isinstance(record, dict):
                continue
            if not record.get("enabled"):
                continue
            if str(record.get("apiKeyHash") or "") != token_hash:
                continue
            record["lastUsedAt"] = now
            _write_store_unlocked(store)
            return {
                "instance": str(instance_name),
                "instanceId": str(record.get("instanceId") or instance_name),
                "maskedApiKey": _masked(token_clean),
            }
    return None
