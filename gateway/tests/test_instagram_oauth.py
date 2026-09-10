from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.services.clients import ClientService
from app.services.connection_registry import ConnectionRegistry
from app.services.connections import ConnectionService, UnsupportedConnectionProviderError
from app.core.config import Settings
from app.services.credential_manager import CredentialManager, ProviderAccountReference
from app.services.core_channel_credentials import CoreChannelCredentialStore
from app.services.core_control_plane import CoreBinding, CoreChannel
from app.services.gateway_settings import GatewaySettingsService
from app.services.instagram_oauth import (
    InstagramAccount,
    InstagramOAuthError,
    InstagramOAuthIntent,
    InstagramOAuthService,
    InstagramOAuthStateStore,
    InstagramOAuthToken,
)
from app.routers import connections as connections_router


class _Runtime:
    async def list_instances(self):
        return []


def _oauth_settings(**changes):
    values = {
        "meta_app_id": "1031982409198448",
        "instagram_app_id": "1787511689049505",
        "meta_app_secret": "fake-meta-app-secret",
        "instagram_app_secret": "fake-instagram-app-secret",
        "meta_redirect_uri": "https://gateway-server.botly.com.ar/connections/meta/instagram/callback",
        "instagram_oauth_scopes": "instagram_business_basic,instagram_business_manage_messages",
        "instagram_oauth_authorize_url": "https://instagram.example/oauth/authorize",
        "instagram_oauth_token_url": "https://instagram.example/oauth/access_token",
        "instagram_graph_api_url": "https://instagram.example",
        "frontend_app_url": "https://gateway.example",
        "meta_signup_timeout_seconds": 3,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _connection_service(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.credential_manager.get_settings",
        lambda: SimpleNamespace(
            official_credentials_path=str(tmp_path / "credentials.json"),
            official_credentials_encryption_key="",
            provider_credentials_encryption_key="dedicated-test-key",
            gateway_api_key="gateway-key",
            environment="test",
        ),
    )
    monkeypatch.setattr(
        "app.services.core_channel_credentials.get_settings",
        lambda: SimpleNamespace(
            core_channel_credentials_path=str(tmp_path / "core-channel-credentials.json"),
            core_channel_credentials_encryption_key="core-channel-test-key",
            gateway_api_key="gateway-key",
            environment="test",
        ),
    )
    settings = GatewaySettingsService(tmp_path / "gateway_settings.json")
    settings.update_channels({"instagram": True})
    registry = ConnectionRegistry(tmp_path / "connections.json")
    client = ClientService(registry).create_client("Tenant A")
    service = ConnectionService(_Runtime(), registry, settings, CredentialManager(), CoreChannelCredentialStore(tmp_path / "core-channel-credentials.json"))
    connection = service.create_connection(client_id=client.id, channel="instagram", provider="meta")
    class _ControlPlane:
        async def discover_channels(self, *, gateway_client_id, channel_type):
            assert gateway_client_id == client.id
            assert channel_type == "instagram"
            return [CoreChannel(id="core-channel-a", name="Botly Instagram", channel_type="instagram", status="active")]

        async def bind(self, *, gateway_client_id, gateway_connection_id, core_channel_id, channel_type):
            assert gateway_client_id == client.id
            assert gateway_connection_id == connection.id
            assert core_channel_id == "core-channel-a"
            assert channel_type == "instagram"
            return CoreBinding(
                id="core-binding-a",
                channel=CoreChannel(id="core-channel-a", name="Botly Instagram", channel_type="instagram", status="active"),
                dispatch_credential="core-channel-credential",
            )

    monkeypatch.setattr(connections_router, "get_core_control_plane_client", lambda: _ControlPlane())
    return service, registry, client, connection


def test_state_is_random_persistent_expiring_and_single_use(tmp_path) -> None:
    store = InstagramOAuthStateStore(tmp_path / "states.json", ttl_seconds=60)
    intent = InstagramOAuthIntent(connection_id="connection-a", client_id="tenant-a", actor_id="user-a")
    first, second = store.create(intent), store.create(intent)

    assert first != second
    assert len(first) >= 40
    assert first not in (tmp_path / "states.json").read_text(encoding="utf-8")
    assert store.consume(first) == intent
    with pytest.raises(InstagramOAuthError, match="invalid or already consumed"):
        store.consume(first)
    with pytest.raises(InstagramOAuthError, match="invalid or already consumed"):
        store.consume("wrong-state")

    expired = InstagramOAuthStateStore(tmp_path / "expired.json", ttl_seconds=1)
    state = expired.create(intent)
    payload = json.loads((tmp_path / "expired.json").read_text(encoding="utf-8"))
    next(iter(payload["states"].values()))["expiresAt"] = 0
    (tmp_path / "expired.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InstagramOAuthError, match="expired"):
        expired.consume(state)


def test_authorization_url_requires_explicit_config_and_uses_server_state() -> None:
    service = InstagramOAuthService(settings_factory=lambda: _oauth_settings())
    url = service.authorization_url(state="server-only-state")
    query = parse_qs(urlparse(url).query)

    assert query["client_id"] == ["1787511689049505"]
    assert query["client_id"] != ["1031982409198448"]
    assert query["redirect_uri"] == ["https://gateway-server.botly.com.ar/connections/meta/instagram/callback"]
    assert query["scope"] == ["instagram_business_basic,instagram_business_manage_messages"]
    assert query["state"] == ["server-only-state"]
    assert "client_secret" not in url
    assert _oauth_settings().meta_app_id == "1031982409198448"
    with pytest.raises(InstagramOAuthError, match="INSTAGRAM_APP_ID"):
        InstagramOAuthService(settings_factory=lambda: _oauth_settings(instagram_app_id="")).authorization_url(state="state")


def test_settings_keep_meta_and_instagram_app_ids_distinct() -> None:
    settings = Settings(
        gateway_api_key="gateway-test-key",
        evolution_api_key="evolution-test-key",
        debug=False,
        meta_app_id="1031982409198448",
        instagram_app_id="1787511689049505",
    )

    assert settings.meta_app_id == "1031982409198448"
    assert settings.instagram_app_id == "1787511689049505"


def test_settings_keep_meta_and_instagram_app_secrets_distinct() -> None:
    settings = Settings(
        gateway_api_key="gateway-test-key",
        evolution_api_key="evolution-test-key",
        meta_app_secret="fake-meta-app-secret",
        instagram_app_secret="fake-instagram-app-secret",
    )

    assert settings.meta_app_secret == "fake-meta-app-secret"
    assert settings.instagram_app_secret == "fake-instagram-app-secret"


def test_token_exchange_and_account_discovery_are_server_side_and_preserve_opaque_ids() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600, "scope": "instagram_business_basic,instagram_business_manage_messages"})
        return httpx.Response(200, json={"id": "111111", "user_id": "17841400000000000", "username": "botly", "account_type": "BUSINESS"})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://instagram.example")
        service = InstagramOAuthService(settings_factory=lambda: _oauth_settings(), client=client)
        token = await service.exchange_code("one-time-code")
        account = await service.discover_account(token.access_token)
        await client.aclose()
        assert token.expires_at is not None
        assert token.granted_scopes == ("instagram_business_basic", "instagram_business_manage_messages")
        assert account.provider_account_id == "17841400000000000"
        assert account.username == "botly"

    asyncio.run(run())
    assert calls[0].method == "POST"
    assert b"client_id=1787511689049505" in calls[0].content
    assert b"client_id=1031982409198448" not in calls[0].content
    assert b"client_secret=fake-instagram-app-secret" in calls[0].content
    assert b"fake-meta-app-secret" not in calls[0].content
    assert b"grant_type=authorization_code" in calls[0].content
    assert b"redirect_uri=https%3A%2F%2Fgateway-server.botly.com.ar%2Fconnections%2Fmeta%2Finstagram%2Fcallback" in calls[0].content
    assert calls[1].headers["authorization"] == "Bearer test-token"


@pytest.mark.parametrize(
    ("payload", "expected_provider_account_id"),
    [
        ({"id": "111111", "user_id": "222222", "account_type": "BUSINESS"}, "222222"),
        ({"user_id": "222222", "account_type": "BUSINESS"}, "222222"),
        ({"id": "111111", "account_type": "BUSINESS"}, "111111"),
        ({"id": "222222", "user_id": "222222", "account_type": "BUSINESS"}, "222222"),
    ],
    ids=("user_id_wins_when_different", "user_id_only", "id_fallback", "matching_ids"),
)
def test_discovery_uses_the_webhook_professional_account_id(payload, expected_provider_account_id) -> None:
    async def run() -> InstagramAccount:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
            base_url="https://instagram.example",
        )
        service = InstagramOAuthService(settings_factory=lambda: _oauth_settings(), client=client)
        account = await service.discover_account("access-token-must-not-be-logged")
        await client.aclose()
        return account

    assert asyncio.run(run()).provider_account_id == expected_provider_account_id


def test_discovery_requires_an_instagram_professional_account_id() -> None:
    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"account_type": "BUSINESS"})),
            base_url="https://instagram.example",
        )
        service = InstagramOAuthService(settings_factory=lambda: _oauth_settings(), client=client)
        with pytest.raises(InstagramOAuthError, match="No supported Instagram professional account") as exc:
            await service.discover_account("access-token-must-not-be-logged")
        await client.aclose()
        assert exc.value.status_code == 422

    asyncio.run(run())


def test_token_exchange_requires_instagram_secret_not_meta_secret() -> None:
    service = InstagramOAuthService(settings_factory=lambda: _oauth_settings(instagram_app_secret=""))

    with pytest.raises(InstagramOAuthError, match="INSTAGRAM_APP_SECRET") as exc:
        asyncio.run(service.exchange_code("one-time-code"))

    assert "fake-meta-app-secret" not in str(exc.value)


def test_token_exchange_error_never_serializes_client_secrets() -> None:
    async def run() -> InstagramOAuthError:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(400, json={"error": {"message": "provider detail"}})),
            base_url="https://instagram.example",
        )
        service = InstagramOAuthService(settings_factory=lambda: _oauth_settings(), client=client)
        with pytest.raises(InstagramOAuthError) as exc:
            await service.exchange_code("one-time-code")
        await client.aclose()
        return exc.value

    error = asyncio.run(run())

    assert "fake-instagram-app-secret" not in str(error)
    assert "fake-meta-app-secret" not in str(error)


def test_default_token_exchange_endpoint_is_instagram_api() -> None:
    settings = Settings(gateway_api_key="gateway-test-key", evolution_api_key="evolution-test-key")

    assert settings.instagram_oauth_token_url == "https://api.instagram.com/oauth/access_token"


@pytest.mark.parametrize(
    "response, expected",
    [
        ({}, "no access token"),
        (None, "malformed"),
    ],
)
def test_token_exchange_rejects_malformed_or_missing_token(response, expected) -> None:
    async def run():
        def handler(_request):
            return httpx.Response(200, json=response) if response is not None else httpx.Response(200, content=b"not-json")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://instagram.example")
        service = InstagramOAuthService(settings_factory=lambda: _oauth_settings(), client=client)
        with pytest.raises(InstagramOAuthError, match=expected):
            await service.exchange_code("code")
        await client.aclose()

    asyncio.run(run())


def test_connection_binding_is_tenant_safe_and_disconnect_removes_credential(monkeypatch, tmp_path) -> None:
    service, registry, tenant_a, connection_a = _connection_service(monkeypatch, tmp_path)
    tenant_b = ClientService(registry).create_client("Tenant B")
    connection_b = service.create_connection(client_id=tenant_b.id, channel="instagram", provider="meta")
    account = ProviderAccountReference("meta", "instagram", "17841400000000000")
    service._credentials.upsert_provider_credentials(
        account=account,
        access_token="token-for-account",
        access_token_ref="meta://instagram/17841400000000000/token",
        source="test",
        scopes=("instagram_business_basic", "instagram_business_manage_messages"),
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    )
    bound = service.bind_instagram_provider_account(
        connection_id=connection_a.id,
        account=account,
        metadata={"username": "tenant_a"},
        required_scopes=("instagram_business_basic", "instagram_business_manage_messages"),
    )

    assert bound.client_id == tenant_a.id
    assert bound.provider_account["providerAccountId"] == "17841400000000000"
    assert service.instagram_readiness(connection_a.id, required_scopes=("instagram_business_basic",))["state"] == "ready"
    with pytest.raises(UnsupportedConnectionProviderError, match="already bound"):
        service.bind_instagram_provider_account(
            connection_id=connection_b.id,
            account=account,
            metadata={},
            required_scopes=(),
        )

    disconnected = service.disconnect_instagram_connection(connection_a.id)
    assert disconnected.status.state == "disconnected"
    assert disconnected.provider_account is None
    assert service._credentials.get_provider_credentials(account) is None


def test_reauthorization_rebinds_the_webhook_account_and_preserves_the_core_binding(monkeypatch, tmp_path) -> None:
    service, _, _, connection = _connection_service(monkeypatch, tmp_path)
    old_account = ProviderAccountReference("meta", "instagram", "111111")
    refreshed_account = ProviderAccountReference("meta", "instagram", "222222")
    for account in (old_account, refreshed_account):
        service._credentials.upsert_provider_credentials(
            account=account,
            access_token=f"token-for-{account.provider_account_id}",
            access_token_ref=f"meta://instagram/{account.provider_account_id}/token",
            source="test",
            scopes=("instagram_business_basic", "instagram_business_manage_messages"),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        )
    service.bind_instagram_provider_account(connection_id=connection.id, account=old_account, metadata={}, required_scopes=())
    service.bind_instagram_core_channel(
        connection_id=connection.id,
        core_channel_id="core-channel-a",
        dispatch_credential="core-credential-a",
        core_binding_id="core-binding-a",
    )

    rebound = service.bind_instagram_provider_account(
        connection_id=connection.id,
        account=refreshed_account,
        metadata={},
        required_scopes=("instagram_business_basic", "instagram_business_manage_messages"),
    )

    assert rebound.provider_account == {
        "provider": "meta", "channelType": "instagram", "providerAccountId": "222222", "metadata": {}
    }
    assert rebound.core_channel and rebound.core_channel["channelId"] == "core-channel-a"
    assert service.instagram_readiness(connection.id, required_scopes=("instagram_business_manage_messages",))["ready"] is True


def test_readiness_handles_missing_scopes_and_expired_credentials(monkeypatch, tmp_path) -> None:
    service, _, _, connection = _connection_service(monkeypatch, tmp_path)
    account = ProviderAccountReference("meta", "instagram", "178400012345678")
    credentials = service._credentials
    credentials.upsert_provider_credentials(
        account=account, access_token="token", access_token_ref="meta://instagram/a/token", source="test", scopes=("instagram_business_basic",)
    )
    service.bind_instagram_provider_account(connection_id=connection.id, account=account, metadata={}, required_scopes=())
    assert service.instagram_readiness(connection.id, required_scopes=("instagram_business_manage_messages",))["state"] == "missing_scopes"

    credentials.upsert_provider_credentials(
        account=account,
        access_token="token-2",
        access_token_ref="meta://instagram/a/token",
        source="test",
        scopes=("instagram_business_basic", "instagram_business_manage_messages"),
        expires_at="2000-01-01T00:00:00Z",
    )
    assert service.instagram_readiness(connection.id, required_scopes=("instagram_business_basic",))["state"] == "expired"


def test_provider_credentials_require_dedicated_production_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.services.credential_manager.get_settings",
        lambda: SimpleNamespace(
            official_credentials_path=str(tmp_path / "credentials.json"),
            official_credentials_encryption_key="",
            provider_credentials_encryption_key="",
            gateway_api_key="must-not-be-used-in-production",
            environment="production",
        ),
    )
    with pytest.raises(RuntimeError, match="PROVIDER_CREDENTIALS_ENCRYPTION_KEY"):
        CredentialManager().upsert_provider_credentials(
            account=ProviderAccountReference("meta", "instagram", "178400012345678"),
            access_token="secret",
            access_token_ref="meta://instagram/account/token",
            source="test",
        )


def test_callback_uses_state_tenant_binding_not_client_supplied_ids(monkeypatch, tmp_path) -> None:
    service, registry, client, connection = _connection_service(monkeypatch, tmp_path)
    states = InstagramOAuthStateStore(tmp_path / "callback_states.json")
    state = states.create(InstagramOAuthIntent(connection.id, client.id, "actor-a"))

    class _OAuth:
        def requested_scopes(self):
            return ("instagram_business_basic",)

        async def exchange_code(self, _code):
            from app.services.instagram_oauth import InstagramOAuthToken
            return InstagramOAuthToken("callback-token", None, ("instagram_business_basic",))

        async def discover_account(self, _token):
            from app.services.instagram_oauth import InstagramAccount
            return InstagramAccount("17841400000000000", username="bound-by-state", account_type="BUSINESS")

    monkeypatch.setattr(connections_router, "_service", service)
    monkeypatch.setattr(connections_router, "_instagram_oauth_states", states)
    monkeypatch.setattr(connections_router, "_instagram_oauth", _OAuth())
    monkeypatch.setattr(connections_router, "get_credential_manager", lambda: service._credentials)

    result = asyncio.run(connections_router.instagram_oauth_callback(state=state, code="code", error=None, error_description=None))
    assert result["ok"] is True
    assert result["connection"]["client_id"] == client.id
    assert result["connection"]["provider_account"]["providerAccountId"] == "17841400000000000"
    assert result["connection"]["core_channel"] == {"channelId": "core-channel-a", "name": "Botly Instagram", "configured": True}
    assert result["connection"]["status"]["state"] == "connected"

    mismatched = states.create(InstagramOAuthIntent(connection.id, "tenant-b", "actor-b"))
    with pytest.raises(Exception) as exc:
        asyncio.run(connections_router.instagram_oauth_callback(state=mismatched, code="code", error=None, error_description=None))
    assert getattr(exc.value, "status_code", None) == 403


def test_callback_requires_exactly_one_active_core_channel(monkeypatch, tmp_path) -> None:
    service, _, client, connection = _connection_service(monkeypatch, tmp_path)
    states = InstagramOAuthStateStore(tmp_path / "ambiguous-core-channel-states.json")
    state = states.create(InstagramOAuthIntent(connection.id, client.id, "actor-a"))

    class _OAuth:
        def requested_scopes(self):
            return ("instagram_business_basic",)

        async def exchange_code(self, _code):
            return InstagramOAuthToken("callback-token", None, ("instagram_business_basic",))

        async def discover_account(self, _token):
            return InstagramAccount("17841400000000000", account_type="BUSINESS")

    class _AmbiguousControlPlane:
        async def discover_channels(self, **_kwargs):
            return [
                CoreChannel(id="core-a", name="Instagram A", channel_type="instagram", status="active"),
                CoreChannel(id="core-b", name="Instagram B", channel_type="instagram", status="active"),
            ]

        async def bind(self, **_kwargs):
            raise AssertionError("an ambiguous Core channel set must never be bound")

    monkeypatch.setattr(connections_router, "_service", service)
    monkeypatch.setattr(connections_router, "_instagram_oauth_states", states)
    monkeypatch.setattr(connections_router, "_instagram_oauth", _OAuth())
    monkeypatch.setattr(connections_router, "get_credential_manager", lambda: service._credentials)
    monkeypatch.setattr(connections_router, "get_core_control_plane_client", lambda: _AmbiguousControlPlane())

    with pytest.raises(Exception) as exc:
        asyncio.run(connections_router.instagram_oauth_callback(state=state, code="code", error=None, error_description=None))

    assert getattr(exc.value, "status_code", None) == 409
    assert service.instagram_core_channel_binding(connection.id) is None
    assert asyncio.run(service.get_connection(connection.id)).status.state == "connecting"


def test_authorize_route_requires_a_meta_instagram_connection_and_creates_bound_state(monkeypatch, tmp_path) -> None:
    service, _, client, connection = _connection_service(monkeypatch, tmp_path)
    states = InstagramOAuthStateStore(tmp_path / "authorize_states.json")
    oauth = InstagramOAuthService(settings_factory=lambda: _oauth_settings())
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="operator-a", role="operator")))

    monkeypatch.setattr(connections_router, "_service", service)
    monkeypatch.setattr(connections_router, "_instagram_oauth_states", states)
    monkeypatch.setattr(connections_router, "_instagram_oauth", oauth)
    monkeypatch.setattr(connections_router, "get_gateway_settings_service", lambda: service._gateway_settings)

    response = asyncio.run(connections_router.authorize_instagram(request=request, connection_id=connection.id))
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    intent = states.consume(state)

    assert response.status_code == 307
    assert intent.connection_id == connection.id
    assert intent.client_id == client.id
    assert intent.actor_id == "operator-a"


def test_ui_callback_is_opt_in_and_never_puts_oauth_data_in_the_redirect(monkeypatch, tmp_path) -> None:
    service, _, client, connection = _connection_service(monkeypatch, tmp_path)
    states = InstagramOAuthStateStore(tmp_path / "ui_callback_states.json")
    state = states.create(InstagramOAuthIntent(connection.id, client.id, "actor-a", ui_return=True))

    class _OAuth:
        def requested_scopes(self):
            return ("instagram_business_basic",)

        async def exchange_code(self, _code):
            from app.services.instagram_oauth import InstagramOAuthToken
            return InstagramOAuthToken("callback-token", None, ("instagram_business_basic",))

        async def discover_account(self, _token):
            from app.services.instagram_oauth import InstagramAccount
            return InstagramAccount("17841400000000000", username="ui-callback", account_type="BUSINESS")

    monkeypatch.setattr(connections_router, "_service", service)
    monkeypatch.setattr(connections_router, "_instagram_oauth_states", states)
    monkeypatch.setattr(connections_router, "_instagram_oauth", _OAuth())
    monkeypatch.setattr(connections_router, "get_credential_manager", lambda: service._credentials)
    monkeypatch.setattr(
        connections_router,
        "get_settings",
        lambda: SimpleNamespace(public_app_url="https://api.example", frontend_app_url="https://gateway.example"),
    )

    response = asyncio.run(connections_router.instagram_oauth_callback(state=state, code="one-time-code", error=None, error_description=None))

    assert response.status_code == 303
    assert response.headers["location"] == f"https://gateway.example/connections/{connection.id}/instagram/complete?oauth=success"
    assert "one-time-code" not in response.headers["location"]
    assert "callback-token" not in response.headers["location"]


class _CapturedLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields) -> None:
        self.events.append((event, fields))


def _ui_callback_state(monkeypatch, tmp_path):
    service, _, client, connection = _connection_service(monkeypatch, tmp_path)
    states = InstagramOAuthStateStore(tmp_path / "callback_failure_states.json")
    state = states.create(InstagramOAuthIntent(connection.id, client.id, "actor-a", ui_return=True))
    captured = _CapturedLogger()
    monkeypatch.setattr(connections_router, "_service", service)
    monkeypatch.setattr(connections_router, "_instagram_oauth_states", states)
    monkeypatch.setattr(connections_router, "get_credential_manager", lambda: service._credentials)
    monkeypatch.setattr(connections_router, "logger", captured)
    monkeypatch.setattr(
        connections_router,
        "get_settings",
        lambda: SimpleNamespace(public_app_url="https://api.example", frontend_app_url="https://frontend.example"),
    )
    return service, connection, state, captured


def _callback_failure_event(captured: _CapturedLogger, stage: str) -> dict:
    matches = [fields for _, fields in captured.events if fields.get("stage") == stage and fields.get("outcome") == "failed"]
    assert matches, captured.events
    return matches[0]


def test_ui_callback_logs_safe_token_exchange_failure(monkeypatch, tmp_path) -> None:
    _, connection, state, captured = _ui_callback_state(monkeypatch, tmp_path)

    class _OAuth:
        async def exchange_code(self, _code):
            raise InstagramOAuthError(
                "secret-token-and-code-must-not-be-logged",
                status_code=502,
                operation="POST /oauth/access_token",
                provider_http_status=400,
            )

    monkeypatch.setattr(connections_router, "_instagram_oauth", _OAuth())
    response = asyncio.run(connections_router.instagram_oauth_callback(state=state, code="one-time-code-must-not-be-logged", error=None, error_description=None))

    assert response.status_code == 303
    assert response.headers["location"] == f"https://frontend.example/connections/{connection.id}/instagram/complete?oauth=failed"
    event = _callback_failure_event(captured, "token_exchange")
    assert event["provider_http_status"] == 400
    assert event["operation"] == "POST /oauth/access_token"
    assert event["error"] == "Instagram authorization code exchange failed"
    assert "secret-token-and-code-must-not-be-logged" not in json.dumps(captured.events)
    assert "one-time-code-must-not-be-logged" not in json.dumps(captured.events)


def test_ui_callback_logs_safe_account_discovery_failure(monkeypatch, tmp_path) -> None:
    _, connection, state, captured = _ui_callback_state(monkeypatch, tmp_path)

    class _OAuth:
        async def exchange_code(self, _code):
            return InstagramOAuthToken("access-token-must-not-be-logged", None, ("instagram_business_basic",))

        async def discover_account(self, _token):
            raise InstagramOAuthError("access-token-must-not-be-logged", status_code=502, operation="GET /me", provider_http_status=403)

    monkeypatch.setattr(connections_router, "_instagram_oauth", _OAuth())
    response = asyncio.run(connections_router.instagram_oauth_callback(state=state, code="code", error=None, error_description=None))

    assert response.status_code == 303
    assert response.headers["location"] == f"https://frontend.example/connections/{connection.id}/instagram/complete?oauth=failed"
    event = _callback_failure_event(captured, "account_discovery")
    assert event["provider_http_status"] == 403
    assert "access-token-must-not-be-logged" not in json.dumps(captured.events)


def test_ui_callback_logs_credential_persistence_and_binding_failures(monkeypatch, tmp_path) -> None:
    service, connection, state, captured = _ui_callback_state(monkeypatch, tmp_path)

    class _OAuth:
        def requested_scopes(self):
            return ("instagram_business_basic",)

        async def exchange_code(self, _code):
            return InstagramOAuthToken("access-token", None, ("instagram_business_basic",))

        async def discover_account(self, _token):
            return InstagramAccount("17841400000000000", account_type="BUSINESS")

    class _Credentials:
        def upsert_provider_credentials(self, **_kwargs):
            raise RuntimeError("secret-persistence-detail-must-not-be-logged")

    monkeypatch.setattr(connections_router, "_instagram_oauth", _OAuth())
    monkeypatch.setattr(connections_router, "get_credential_manager", lambda: _Credentials())
    persistence_response = asyncio.run(connections_router.instagram_oauth_callback(state=state, code="code", error=None, error_description=None))
    assert persistence_response.status_code == 303
    assert _callback_failure_event(captured, "credential_persistence")["error"] == "Instagram credential persistence failed"
    assert "secret-persistence-detail-must-not-be-logged" not in json.dumps(captured.events)

    binding_state = InstagramOAuthStateStore(tmp_path / "binding_failure_states.json")
    binding_token = binding_state.create(InstagramOAuthIntent(connection.id, connection.client_id, "actor-a", ui_return=True))
    monkeypatch.setattr(connections_router, "_instagram_oauth_states", binding_state)
    monkeypatch.setattr(connections_router, "get_credential_manager", lambda: service._credentials)
    monkeypatch.setattr(
        service,
        "assert_instagram_provider_account_available",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(UnsupportedConnectionProviderError("duplicate")),
    )
    binding_response = asyncio.run(connections_router.instagram_oauth_callback(state=binding_token, code="code", error=None, error_description=None))
    assert binding_response.status_code == 303
    assert _callback_failure_event(captured, "binding")["error"] == "Instagram provider account binding failed"
