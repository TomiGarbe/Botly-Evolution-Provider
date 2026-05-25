from __future__ import annotations

import asyncio
import json
import socket
import time
import traceback
from urllib.parse import urlparse
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.instance_webhooks import append_dispatch_history, build_auth_headers, mark_dispatch_result_ex, mask_headers_for_log

logger = get_logger(__name__)
settings = get_settings()

_RETRY_BACKOFF_SECONDS = [2, 5, 15]
_RETRYABLE_HTTP = {500, 502, 503, 504}
_NON_RETRYABLE_HTTP = {400, 401, 403, 404, 405, 409, 410, 422}


def _is_retryable(status_code: int | None, exc: Exception | None) -> bool:
    if exc is not None:
        return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
    if status_code is None:
        return False
    if status_code in _NON_RETRYABLE_HTTP:
        return False
    return status_code in _RETRYABLE_HTTP


def _status_name(status_code: int | None, exc: Exception | None) -> str:
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        return "connection_fail"
    return f"ok_{status_code}" if status_code and 200 <= status_code < 300 else f"http_{status_code or 0}"


def _should_verbose_dispatch_logs() -> bool:
    return bool(settings.webhook_debug)


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    text = str(payload.get("text") or payload.get("content") or "")
    return {
        "event": payload.get("event"),
        "type": payload.get("type"),
        "subtype": payload.get("subtype"),
        "instance": payload.get("instance"),
        "messageId": message.get("id") or payload.get("messageId"),
        "direction": payload.get("direction"),
        "textPreview": text[:120] if text else "",
    }


def _classify_dispatch_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        raw = str(exc).lower()
        if "name or service not known" in raw or "nodename nor servname provided" in raw:
            return "dns_fail"
        if "connection refused" in raw:
            return "connection_refused"
        if "ssl" in raw or "certificate" in raw:
            return "ssl_fail"
        return "connect_error"
    if isinstance(exc, httpx.ReadError):
        return "read_error"
    if isinstance(exc, httpx.WriteError):
        return "write_error"
    if isinstance(exc, httpx.TransportError):
        return "transport_error"
    if isinstance(exc, json.JSONDecodeError):
        return "serialization_fail"
    return exc.__class__.__name__.lower()


def _safe_traceback(exc: Exception) -> str:
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()[:500]


def _pick_payload_by_filter(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    filters = item.get("eventFilters") if isinstance(item.get("eventFilters"), dict) else {}
    allow_business = bool(filters.get("business", True))
    allow_transport = bool(filters.get("transport", False))
    allow_operational = bool(filters.get("operational", False))
    category = str(payload.get("category") or "")
    if category == "business_message" or category == "business_event":
        return payload if allow_business else None
    if category == "transport":
        return payload if allow_transport else None
    if category == "operational":
        return payload if allow_operational else None
    return payload if allow_business else None


async def dispatch_webhook_with_retry(*, payload: dict[str, Any], request_id: str, item: dict[str, Any], test_mode: bool = False) -> dict[str, Any]:
    instance_name = str(payload.get("instance") or "")
    webhook_id = str(item.get("id") or "")
    dispatch_id = str(payload.get("dispatchId") or f"disp_{request_id}_{webhook_id[:6]}")
    message_id = str(((payload.get("message") or {}).get("id") or payload.get("messageId") or ""))
    url = str(item.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "status": "invalid_url", "statusCode": 0, "retriesUsed": 0, "latencyMs": 0.0}

    target_payload = _pick_payload_by_filter(item, payload)
    if target_payload is None:
        return {"ok": True, "status": "skipped_by_filter", "statusCode": 204, "retriesUsed": 0, "latencyMs": 0.0}

    headers = {"Content-Type": "application/json", **build_auth_headers(item)}
    auth_type = str(item.get("authType") or "NONE").upper()
    verbose = _should_verbose_dispatch_logs()
    payload_json = json.dumps(target_payload, ensure_ascii=True, default=str)
    payload_size = len(payload_json.encode("utf-8"))
    logger.info(
        "bot_webhook_dispatch_request",
        request_id=request_id,
        dispatch_id=dispatch_id,
        instance=instance_name,
        webhook_id=webhook_id,
        message_id=message_id or None,
        url=url,
        method="POST",
        timeout_s=float(settings.instance_webhook_timeout),
        payload_summary=_payload_summary(target_payload),
        payload_size_bytes=payload_size,
        headers=mask_headers_for_log(headers),
        test_mode=test_mode,
    )

    timeout = httpx.Timeout(
        connect=min(5.0, float(settings.instance_webhook_timeout)),
        read=float(settings.instance_webhook_timeout),
        write=min(8.0, float(settings.instance_webhook_timeout)),
        pool=2.0,
    )

    attempts = 1 + len(_RETRY_BACKOFF_SECONDS)
    last_error: str | None = None
    last_code: int | None = None
    last_exc: Exception | None = None
    retries_used = 0
    retryable = False
    started = time.perf_counter()
    debug_enabled = verbose
    if debug_enabled:
        logger.info(
            "bot_webhook_debug_preview",
            request_id=request_id,
            dispatch_id=dispatch_id,
            webhook_id=webhook_id,
            payload_preview=payload_json[:800],
            headers_preview=mask_headers_for_log(headers),
        )

    for attempt in range(1, attempts + 1):
        exc: Exception | None = None
        code: int | None = None
        response_snippet = ""
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if verbose:
                    logger.info(
                        "bot_webhook_dispatch_attempt",
                        request_id=request_id,
                        dispatch_id=dispatch_id,
                        webhook_id=webhook_id,
                        retry_attempt=attempt - 1,
                        attempt=attempt,
                        auth_type=auth_type,
                    )
                resp = await client.post(url, json=target_payload, headers=headers)
            code = int(resp.status_code)
            response_snippet = (resp.text or "")[:220]
            logger.info(
                "bot_webhook_dispatch_response",
                request_id=request_id,
                dispatch_id=dispatch_id,
                webhook_id=webhook_id,
                status_code=code,
                retry_attempt=attempt - 1,
                response_headers=dict(resp.headers) if verbose else {},
                response_body_preview=response_snippet,
            )
            if 200 <= code < 300:
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                mark_dispatch_result_ex(
                    instance_name,
                    webhook_id,
                    status=_status_name(code, None),
                    error=None,
                    status_code=code,
                    latency_ms=latency_ms,
                    retries_used=retries_used,
                    retryable=False,
                )
                logger.info(
                    "bot_webhook_dispatch_success",
                    request_id=request_id,
                    dispatch_id=dispatch_id,
                    instance=instance_name,
                    webhook_id=webhook_id,
                    message_id=message_id or None,
                    status=code,
                    retries_used=retries_used,
                    latency_ms=latency_ms,
                    test_mode=test_mode,
                )
                append_dispatch_history(
                    instance_name,
                    webhook_id,
                    {
                        "timestamp": int(time.time() * 1000),
                        "status": "success",
                        "dispatchId": dispatch_id,
                        "messageId": message_id or None,
                        "eventSubtype": target_payload.get("subtype"),
                        "retryCount": retries_used,
                        "responseCode": code,
                        "durationMs": latency_ms,
                        "webhookUrl": url,
                        "request": {
                            "method": "POST",
                            "headers": mask_headers_for_log(headers),
                            "payloadSummary": _payload_summary(target_payload),
                            "payloadSizeBytes": payload_size,
                        },
                        "response": {
                            "headers": dict(resp.headers),
                            "bodyPreview": response_snippet,
                        },
                    },
                )
                return {"ok": True, "status": f"ok_{code}", "statusCode": code, "retriesUsed": retries_used, "latencyMs": latency_ms}
        except Exception as caught:
            exc = caught
            response_snippet = str(caught)[:220]
            error_type = _classify_dispatch_error(caught)
            logger.error(
                "bot_webhook_dispatch_fail",
                request_id=request_id,
                dispatch_id=dispatch_id,
                webhook_id=webhook_id,
                retry_attempt=attempt - 1,
                error_type=error_type,
                error=response_snippet,
                traceback=_safe_traceback(caught),
            )
            if error_type == "timeout":
                logger.error(
                    "bot_webhook_dispatch_timeout",
                    request_id=request_id,
                    dispatch_id=dispatch_id,
                    webhook_id=webhook_id,
                    retry_attempt=attempt - 1,
                    error=response_snippet,
                )

        retryable = _is_retryable(code, exc)
        last_code = code
        last_exc = exc
        last_error = response_snippet
        final = attempt == attempts or not retryable
        if not final:
            backoff = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
            retries_used += 1
            logger.warning(
                "webhook_dispatch_retry",
                request_id=request_id,
                dispatch_id=dispatch_id,
                instance=instance_name,
                webhook_id=webhook_id,
                message_id=message_id or None,
                attempt=attempt,
                next_attempt=attempt + 1,
                backoff_s=backoff,
                status_code=code,
                retryable=retryable,
                error=response_snippet,
                test_mode=test_mode,
            )
            await asyncio.sleep(backoff)
            continue
        break

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    status_name = _status_name(last_code, last_exc)
    mark_dispatch_result_ex(
        instance_name,
        webhook_id,
        status=status_name,
        error=last_error,
        status_code=last_code,
        latency_ms=latency_ms,
        retries_used=retries_used,
        retryable=retryable,
    )
    if str(status_name).startswith("http_"):
        logger.warning(
            "webhook_dispatch_fail",
            request_id=request_id,
            dispatch_id=dispatch_id,
            instance=instance_name,
            webhook_id=webhook_id,
            message_id=message_id or None,
            status_code=last_code,
            retries_used=retries_used,
            retryable=retryable,
            latency_ms=latency_ms,
            error=last_error,
            test_mode=test_mode,
        )
    else:
        logger.error(
            "webhook_dispatch_fail",
            request_id=request_id,
            dispatch_id=dispatch_id,
            instance=instance_name,
            webhook_id=webhook_id,
            message_id=message_id or None,
            status_code=last_code or 0,
            retries_used=retries_used,
            retryable=retryable,
            latency_ms=latency_ms,
            error=last_error,
            test_mode=test_mode,
        )
    return {"ok": False, "status": status_name, "statusCode": int(last_code or 0), "retriesUsed": retries_used, "latencyMs": latency_ms, "error": last_error}


async def diagnose_webhook_target(url: str, timeout_s: float) -> dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    started_ms = int(time.time() * 1000)
    dns_result: list[str] = []
    tcp_ok = False
    tcp_error = None
    http_result: dict[str, Any] = {}

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        dns_result = sorted({item[4][0] for item in infos if item and item[4]})
    except Exception as exc:
        dns_result = []
        tcp_error = f"dns:{exc}"

    try:
        stream = await asyncio.open_connection(host=host, port=port)
        reader, writer = stream
        writer.close()
        await writer.wait_closed()
        tcp_ok = True
    except Exception as exc:
        tcp_ok = False
        tcp_error = str(exc)

    try:
        timeout = httpx.Timeout(connect=min(5.0, timeout_s), read=timeout_s, write=timeout_s, pool=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        http_result = {
            "ok": True,
            "statusCode": int(resp.status_code),
            "headers": dict(resp.headers),
            "bodyPreview": (resp.text or "")[:240],
        }
    except Exception as exc:
        http_result = {
            "ok": False,
            "errorType": _classify_dispatch_error(exc),
            "error": str(exc),
            "traceback": _safe_traceback(exc),
        }

    return {
        "target": {"host": host, "port": port, "url": url},
        "dns": {"resolved": bool(dns_result), "addresses": dns_result},
        "tcp": {"ok": tcp_ok, "error": tcp_error},
        "http": http_result,
        "timestamp": started_ms,
    }
