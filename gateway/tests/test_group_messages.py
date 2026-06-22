from __future__ import annotations

import sys
import os
from types import ModuleType

os.environ["DEBUG"] = "false"
os.environ.setdefault("GATEWAY_API_KEY", "test-gateway-key")
os.environ.setdefault("EVOLUTION_API_KEY", "test-evolution-key")


class _FakeLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


_fake_structlog = ModuleType("structlog")
_fake_structlog.get_logger = lambda *args, **kwargs: _FakeLogger()
sys.modules.setdefault("structlog", _fake_structlog)

from app.services.event_pipeline import process_incoming_webhook, settings
from app.services.group_messages import group_message_audit_context, is_group_message


def _group_payload() -> dict:
    return {
        "event": "messages.upsert",
        "instance": "botly",
        "data": {
            "key": {
                "id": "msg-group-1",
                "remoteJid": "120363000000000000@g.us",
                "participant": "5491111111111@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {"conversation": "hola grupo"},
            "messageType": "conversation",
            "pushName": "Juan",
            "messageTimestamp": "1782084204",
        },
    }


def test_is_group_message_detects_evolution_group_jid():
    payload = _group_payload()

    assert is_group_message(payload) is True
    assert group_message_audit_context(payload) == {
        "groupId": "120363000000000000@g.us",
        "sender": "5491111111111@s.whatsapp.net",
        "reason": "group_messages_disabled",
    }


def test_process_incoming_webhook_ignores_group_before_persist_and_dispatch(monkeypatch):
    monkeypatch.setattr(settings, "enable_group_messages", False)

    result = process_incoming_webhook(_group_payload(), request_id="req-group")

    assert result["status"] == "ignored_group"
    assert result["trace"]["route"]["reason"] == "group_messages_disabled"
