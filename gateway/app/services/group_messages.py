from __future__ import annotations

from typing import Any

GROUP_JID_SUFFIX = "@g.us"


def is_group_message(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if _truthy(_find_first(payload, ("isGroup",), ("is_group",), ("data", "isGroup"), ("context", "chat", "isGroup"))):
        return True
    for value in _iter_candidate_values(payload):
        if _is_group_jid(value):
            return True
    return False


def group_message_audit_context(payload: dict[str, Any]) -> dict[str, Any]:
    group_id = _first_group_jid(payload)
    sender = _first_sender(payload)
    return {
        "groupId": group_id,
        "sender": sender,
        "reason": "group_messages_disabled",
    }


def _iter_candidate_values(payload: dict[str, Any]) -> list[Any]:
    return [
        payload.get("remoteJid"),
        payload.get("chatId"),
        payload.get("conversationId"),
        payload.get("groupId"),
        _find_first(payload, ("data", "remoteJid")),
        _find_first(payload, ("data", "chatId")),
        _find_first(payload, ("data", "conversationId")),
        _find_first(payload, ("data", "groupId")),
        _find_first(payload, ("key", "remoteJid")),
        _find_first(payload, ("data", "key", "remoteJid")),
        _find_first(payload, ("message", "from")),
        _find_first(payload, ("context", "chat", "jid")),
        _find_first(payload, ("context", "chat", "groupId")),
        _find_first(payload, ("raw", "data", "key", "remoteJid")),
    ]


def _first_group_jid(payload: dict[str, Any]) -> str | None:
    for value in _iter_candidate_values(payload):
        if _is_group_jid(value):
            return str(value).strip()
    return None


def _first_sender(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("participant"),
        _find_first(payload, ("key", "participant")),
        _find_first(payload, ("data", "key", "participant")),
        _find_first(payload, ("message", "participant")),
        _find_first(payload, ("context", "chat", "participant")),
        _find_first(payload, ("raw", "data", "key", "participant")),
        payload.get("sender"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _find_first(value: Any, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current = value
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found and current is not None:
            return current
    return None


def _is_group_jid(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().endswith(GROUP_JID_SUFFIX)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
