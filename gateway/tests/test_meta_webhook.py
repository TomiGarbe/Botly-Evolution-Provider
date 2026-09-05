from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.routers import meta_webhook
from app.services import credential_manager
from app.services.instagram_webhook import InstagramWebhookError


def _settings(
    *,
    verify_token: str = "verify-token",
    app_secret: str = "app-secret",
    instagram_app_secret: str = "instagram-app-secret",
) -> SimpleNamespace:
    return SimpleNamespace(
        meta_webhook_verify_token=verify_token,
        meta_webhook_require_signature=True,
        meta_app_secret=app_secret,
        instagram_app_secret=instagram_app_secret,
        bot_webhook_max_queue=200,
    )


def _signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_shared_meta_webhook_requires_the_secret_for_its_object(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(meta_webhook, "get_settings", lambda: settings)
    monkeypatch.setattr(meta_webhook, "process_instagram_webhook", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(meta_webhook, "get_core_inbound_dispatcher", lambda: SimpleNamespace(persist_many=lambda _events: []))
    client = TestClient(app)

    cases = [
        ("instagram", settings.instagram_app_secret),
        ("whatsapp_business_account", settings.meta_app_secret),
    ]
    for webhook_object, matching_secret in cases:
        body = json.dumps({"object": webhook_object, "entry": []}, separators=(",", ":")).encode("utf-8")
        other_secret = settings.meta_app_secret if webhook_object == "instagram" else settings.instagram_app_secret

        assert client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": _signature(body, matching_secret)}).status_code == 200
        assert client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": _signature(body, other_secret)}).status_code == 401
        assert client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": "sha256=invalid"}).status_code == 401
        assert client.post("/webhooks/meta", content=body).status_code == 401


def test_instagram_resolution_rejection_logs_only_safe_lookup_context(monkeypatch) -> None:
    class _Logger:
        def __init__(self):
            self.warnings = []

        def info(self, *_args, **_kwargs):
            pass

        def warning(self, _event, **fields):
            self.warnings.append(fields)

    logger = _Logger()
    settings = _settings()
    body = json.dumps({"object": "instagram", "entry": [{"id": "222222", "messaging": []}]}, separators=(",", ":")).encode()
    monkeypatch.setattr(meta_webhook, "get_settings", lambda: settings)
    monkeypatch.setattr(meta_webhook, "logger", logger)
    monkeypatch.setattr(
        meta_webhook,
        "process_instagram_webhook",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            InstagramWebhookError(
                "Instagram connection cannot receive webhooks",
                status_code=404,
                provider_account_id="222222",
                connection_lookup_result="not_found",
            )
        ),
    )

    response = TestClient(app).post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": _signature(body, settings.instagram_app_secret)})

    assert response.status_code == 404
    rejection = next(item for item in logger.warnings if item.get("connection_lookup_result") == "not_found")
    assert rejection["provider"] == "meta"
    assert rejection["channel_type"] == "instagram"
    assert rejection["provider_account_id"] == "222222"
    assert rejection["request_id"]


def test_meta_webhook_returns_the_exact_challenge(monkeypatch) -> None:
    monkeypatch.setattr(meta_webhook, "get_settings", lambda: _settings())
    client = TestClient(app)

    response = client.get(
        "/webhooks/meta",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-token", "hub.challenge": "123456"},
    )

    assert response.status_code == 200
    assert response.text == "123456"
    assert response.headers["content-type"].startswith("text/plain")
    assert client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123456"}).status_code == 403


def test_meta_webhook_accepts_signed_message_status_and_error(monkeypatch, tmp_path) -> None:
    settings = _settings()
    store_path = tmp_path / "credentials.json"
    monkeypatch.setattr(meta_webhook, "get_settings", lambda: settings)
    monkeypatch.setattr(
        credential_manager,
        "get_settings",
        lambda: SimpleNamespace(
            official_credentials_path=str(store_path),
            official_credentials_encryption_key="test-encryption-key",
        ),
    )
    credential_manager.get_credential_manager().upsert_official_credentials(
        instance_name="cloud_test",
        access_token="access-token",
        phone_number_id="phone_123",
        business_account_id="waba_123",
        source="test",
    )
    now = str(int(time.time()))
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "phone_123"},
            "messages": [{"from": "5491100000000", "id": "wamid.message", "timestamp": now, "type": "text", "text": {"body": "hola"}}],
            "statuses": [{"id": "wamid.status", "recipient_id": "5491100000000", "timestamp": now, "status": "delivered"}],
            "errors": [{"code": 131000}],
        }}]}],
    }
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

    response = TestClient(app).post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": signature})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "messages": 1, "statuses": 1, "changes": 1, "errors": 1, "unmapped": 0}
    assert TestClient(app).post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
