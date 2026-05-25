from __future__ import annotations

import re
import time
from typing import Any

from app.core.config import get_settings
from app.services import evolution

_TOKEN_CACHE: dict[str, Any] = {"expiresAt": 0.0, "byInstance": {}}


def _mask_prefix(value: str, size: int = 8) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw[:size]


def extract_evolution_auth(request_headers: Any, payload: dict[str, Any]) -> dict[str, str]:
    header_candidates = [
        ("apikey", request_headers.get("apikey")),
        ("x-api-key", request_headers.get("x-api-key")),
        ("authorization", request_headers.get("authorization")),
    ]
    provided = ""
    source = "none"
    normalized = "raw"
    for key, candidate in header_candidates:
        if not candidate:
            continue
        raw = str(candidate).strip()
        value = raw
        if key == "authorization":
            value = re.sub(r"^Bearer\s+", "", raw, flags=re.IGNORECASE).strip()
            normalized = "bearer_strip"
        if value:
            provided = value
            source = f"header.{key}"
            break
    if not provided:
        body_key = str(payload.get("apikey") or "").strip()
        if body_key:
            provided = body_key
            source = "payload.apikey"
    if not provided:
        query_key = str(payload.get("apiKey") or "").strip()
        if query_key:
            provided = query_key
            source = "payload.apiKey"
    return {"providedKey": provided, "source": source, "normalized": normalized}


async def _instance_tokens() -> dict[str, str]:
    settings = get_settings()
    now = time.time()
    if now < float(_TOKEN_CACHE["expiresAt"] or 0):
        return dict(_TOKEN_CACHE["byInstance"])
    items = await evolution.fetch_instances()
    by_instance: dict[str, str] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("instanceName") or "").strip()
            token = str(item.get("token") or "").strip()
            if name and token:
                by_instance[name] = token
    _TOKEN_CACHE["byInstance"] = by_instance
    _TOKEN_CACHE["expiresAt"] = now + max(10, int(settings.evolution_auth_cache_ttl_seconds or 45))
    return by_instance


async def validate_evolution_auth(payload: dict[str, Any], provided_key: str) -> dict[str, Any]:
    settings = get_settings()
    instance = str(payload.get("instance") or "unknown")
    expected_global = str(settings.evolution_api_key or "").strip()
    expected_instance = ""
    instance_match = False

    tokens = await _instance_tokens()
    expected_instance = str(tokens.get(instance) or "")
    if expected_instance and provided_key and provided_key == expected_instance:
        instance_match = True

    global_match = bool(expected_global and provided_key and provided_key == expected_global)
    accepted = global_match or instance_match
    mode = "none"
    if global_match:
        mode = "global_api_key"
    elif instance_match:
        mode = "instance_token"

    return {
        "accepted": accepted,
        "mode": mode,
        "expectedGlobalPrefix": _mask_prefix(expected_global),
        "expectedInstancePrefix": _mask_prefix(expected_instance),
        "receivedPrefix": _mask_prefix(provided_key),
        "hasExpectedGlobal": bool(expected_global),
        "hasExpectedInstanceToken": bool(expected_instance),
    }


async def auth_runtime_snapshot() -> dict[str, Any]:
    settings = get_settings()
    tokens = await _instance_tokens()
    return {
        "expectedGlobalPrefix": _mask_prefix(str(settings.evolution_api_key or "").strip()),
        "instances": {name: _mask_prefix(token) for name, token in tokens.items()},
        "allowInsecure": bool(settings.allow_insecure_evolution_webhooks),
        "cacheExpiresAtEpoch": int(_TOKEN_CACHE["expiresAt"] or 0),
    }
