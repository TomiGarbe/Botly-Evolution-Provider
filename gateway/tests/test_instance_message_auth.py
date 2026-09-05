from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

import app.middleware.auth as auth_middleware
from app.middleware.auth import AuthMiddleware
from app.routers.messages import _require_instance_token_scope


def _request(path: str, token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
    )


def test_instance_token_is_accepted_only_for_unified_message_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(auth_middleware, "get_settings", lambda: SimpleNamespace(gateway_api_key="global-token"))
    monkeypatch.setattr(
        auth_middleware,
        "authenticate_instance_token",
        lambda token: {"instance": "connection_a"} if token == "instance-token" else None,
    )
    request = _request("/messages/connection_a", "instance-token")

    response = asyncio.run(AuthMiddleware(app=lambda *_: None).dispatch(request, lambda _request: Response(status_code=204)))

    assert response.status_code == 204
    assert request.state.auth_method == "instance_api_key"
    assert request.state.auth_instance == "connection_a"


def test_instance_token_is_accepted_for_canonical_outbound_only(monkeypatch) -> None:
    monkeypatch.setattr(auth_middleware, "get_settings", lambda: SimpleNamespace(gateway_api_key="global-token"))
    monkeypatch.setattr(
        auth_middleware,
        "authenticate_instance_token",
        lambda token: {"instance": "connection_a"} if token == "instance-token" else None,
    )
    request = _request("/v1/outbound/messages", "instance-token")

    response = asyncio.run(AuthMiddleware(app=lambda *_: None).dispatch(request, lambda _request: Response(status_code=204)))

    assert response.status_code == 204
    assert request.state.auth_method == "instance_api_key"
    assert request.state.auth_instance == "connection_a"


def test_instance_token_cannot_access_another_instance() -> None:
    request = _request("/messages/connection_b", "instance-token")
    request.state.auth_instance = "connection_a"

    try:
        _require_instance_token_scope(request, "connection_b")
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected instance scope validation to reject the request")
