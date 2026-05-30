from __future__ import annotations

import asyncio
import base64
import re
import random
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_DATA_URI_RE = re.compile(r"^data:[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+-]*/[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+-]*;base64,")
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class EvolutionError(Exception):
    message: str
    status_code: int = 502
    detail: Any | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


def _build_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=25.0, write=15.0, pool=5.0)


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is not None:
        return _client

    async with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = httpx.AsyncClient(
                base_url=settings.evolution_url,
                headers={"apikey": settings.evolution_api_key, "Content-Type": "application/json"},
                timeout=_build_timeout(),
            )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        for key in ("message", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return response.text[:300] or f"HTTP {response.status_code}"


async def _request(method: str, path: str, *, retries: int = 1, **kwargs) -> Any:
    client = await get_client()

    for attempt in range(retries + 1):
        try:
            response = await client.request(method, path, **kwargs)

            if response.status_code >= 500 and attempt < retries:
                backoff = 0.25 * (2**attempt) + random.uniform(0, 0.2)
                logger.warning(
                    "evolution_request_retry_server_error",
                    method=method,
                    path=path,
                    status=response.status_code,
                    attempt=attempt + 1,
                    backoff=round(backoff, 3),
                )
                await asyncio.sleep(backoff)
                continue

            response.raise_for_status()
            if response.content:
                try:
                    return response.json()
                except Exception:
                    return {"ok": True, "raw": response.text}
            return {"ok": True}

        except httpx.TimeoutException as exc:
            retryable = attempt < retries
            logger.warning(
                "evolution_request_timeout",
                method=method,
                path=path,
                attempt=attempt + 1,
                retryable=retryable,
            )
            if retryable:
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            raise EvolutionError(
                message=f"Evolution timeout on {method} {path}",
                status_code=504,
                retryable=True,
            ) from exc

        except httpx.HTTPStatusError as exc:
            response = exc.response
            message = _extract_error_message(response)
            retryable = response.status_code >= 500
            logger.info(
                "evolution_response",
                method=method,
                path=path,
                status=response.status_code,
                body=response.text,
            )
            logger.warning(
                "evolution_request_http_error",
                method=method,
                path=path,
                status=response.status_code,
                message=message,
                attempt=attempt + 1,
            )
            raise EvolutionError(
                message=f"Evolution HTTP {response.status_code}: {message}",
                status_code=response.status_code,
                detail={"method": method, "path": path, "response": message},
                retryable=retryable,
            ) from exc

        except httpx.HTTPError as exc:
            retryable = attempt < retries
            logger.warning(
                "evolution_request_transport_error",
                method=method,
                path=path,
                attempt=attempt + 1,
                retryable=retryable,
                error=str(exc),
            )
            if retryable:
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            raise EvolutionError(
                message=f"Evolution transport error on {method} {path}: {exc}",
                status_code=502,
                retryable=True,
            ) from exc


# Instancias


async def create_instance(instance_name: str, qrcode: bool = True, token: str | None = None) -> dict:
    payload: dict[str, Any] = {
        "instanceName": instance_name,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": qrcode,
    }
    if token:
        payload["token"] = token
    logger.info("instance_create_requested", instance=instance_name)
    return await _request("POST", "/instance/create", json=payload, retries=1)


async def get_qr(instance_name: str) -> dict:
    return await _request("GET", f"/instance/connect/{instance_name}", retries=1)


async def get_connection_state(instance_name: str) -> dict:
    return await _request("GET", f"/instance/connectionState/{instance_name}", retries=1)


async def fetch_instances() -> list:
    return await _request("GET", "/instance/fetchInstances", retries=1)


async def restart_instance(instance_name: str) -> dict:
    logger.info("instance_restart_requested", instance=instance_name)
    try:
        return await _request("POST", f"/instance/restart/{instance_name}", retries=1)
    except EvolutionError as exc:
        # Algunas versiones usan PUT o no exponen este endpoint.
        if exc.status_code in (404, 405):
            logger.warning("instance_restart_fallback_put", instance=instance_name)
            return await _request("PUT", f"/instance/restart/{instance_name}", retries=1)
        raise


async def logout_instance(instance_name: str) -> dict:
    logger.info("instance_logout_requested", instance=instance_name)
    return await _request("DELETE", f"/instance/logout/{instance_name}", retries=1)


async def delete_instance(instance_name: str) -> dict:
    logger.warning("instance_delete_requested", instance=instance_name)
    return await _request("DELETE", f"/instance/delete/{instance_name}", retries=1)


# Mensajes


async def send_text(instance_name: str, number: str, text: str) -> dict:
    logger.info("text_send_requested", instance=instance_name, recipient=number)
    return await _request(
        "POST",
        f"/message/sendText/{instance_name}",
        json={"number": number, "text": text},
        retries=0,
    )


async def send_media(
    instance_name: str,
    number: str,
    media_payload: str,
    mediatype: str,
    mimetype: str,
    file_name: str,
    caption: str = "",
) -> dict:
    raw_media = (media_payload or "").strip()
    if not raw_media:
        raise EvolutionError(message="Payload media invalido: contenido vacio", status_code=400, retryable=False)

    if _HTTP_URL_RE.match(raw_media):
        payload_real = {"number": number, "mediatype": mediatype, "media": raw_media}
        if file_name:
            payload_real["fileName"] = file_name
        if caption:
            payload_real["caption"] = caption
        if mimetype:
            payload_real["mimetype"] = mimetype
        return await _request("POST", f"/message/sendMedia/{instance_name}", json=payload_real, retries=0)

    media_prefix_ok = bool(_DATA_URI_RE.match(raw_media))
    media_b64 = raw_media.split(",", 1)[1] if media_prefix_ok else raw_media
    try:
        base64.b64decode(media_b64, validate=True)
        media_b64_ok = True
    except Exception:
        media_b64_ok = False

    if not media_b64_ok:
        raise EvolutionError(
            message="Payload media invalido: se esperaba base64 valido (raw o data URI)",
            status_code=400,
            detail={"media_prefix_ok": media_prefix_ok, "media_b64_ok": media_b64_ok},
            retryable=False,
        )

    payload_b = {"number": number, "mediatype": mediatype, "media": media_b64}
    if file_name:
        payload_b["fileName"] = file_name
    attempts: list[dict[str, Any]] = []

    # Fase A: data URI (si estaba presente originalmente)
    if media_prefix_ok:
        payload_a = dict(payload_b)
        payload_a["media"] = raw_media
        try:
            result = await _request("POST", f"/message/sendMedia/{instance_name}", json=payload_a, retries=0)
            logger.info(
                "evolution_send_media_ab",
                endpoint=f"/message/sendMedia/{instance_name}",
                test="A_data_uri",
                status=201,
                accepted_format="data_uri",
            )
            return result
        except EvolutionError as exc:
            attempts.append({"test": "A_data_uri", "status": exc.status_code, "error": str(exc)})
            logger.warning(
                "evolution_send_media_ab",
                endpoint=f"/message/sendMedia/{instance_name}",
                test="A_data_uri",
                status=exc.status_code,
                error=str(exc),
            )

    # Fase B: base64 raw (fallback y formato preferido)
    try:
        result = await _request("POST", f"/message/sendMedia/{instance_name}", json=payload_b, retries=0)
        logger.info(
            "evolution_send_media_ab",
            endpoint=f"/message/sendMedia/{instance_name}",
            test="B_raw_base64",
            status=201,
            accepted_format="raw_base64",
        )
        return result
    except EvolutionError as exc:
        attempts.append({"test": "B_raw_base64", "status": exc.status_code, "error": str(exc)})
        logger.warning(
            "evolution_send_media_ab",
            endpoint=f"/message/sendMedia/{instance_name}",
            test="B_raw_base64",
            status=exc.status_code,
            error=str(exc),
        )
        raise EvolutionError(
            message=f"Evolution rechazo media en A/B: {attempts}",
            status_code=exc.status_code,
            detail={"attempts": attempts},
            retryable=exc.retryable,
        ) from exc


async def send_buttons(instance_name: str, payload: dict) -> dict:
    return await _request("POST", f"/message/sendButtons/{instance_name}", json=payload, retries=0)


async def send_list(instance_name: str, payload: dict) -> dict:
    return await _request("POST", f"/message/sendList/{instance_name}", json=payload, retries=0)


# Webhooks


async def set_webhook(instance_name: str, url: str, events: list[str]) -> dict:
    payload = {
        "webhook": {
            "enabled": True,
            "url": url,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": events,
        }
    }
    return await _request("POST", f"/webhook/set/{instance_name}", json=payload, retries=1)


async def get_webhook(instance_name: str) -> dict:
    return await _request("GET", f"/webhook/find/{instance_name}", retries=1)


# Utilidades


async def check_whatsapp_numbers(instance_name: str, numbers: list[str]) -> list:
    return await _request(
        "POST",
        f"/chat/whatsappNumbers/{instance_name}",
        json={"numbers": numbers},
        retries=0,
    )


def _find_base64_candidate(node: Any) -> str | None:
    if isinstance(node, str):
        text = node.strip()
        if text.startswith("data:") and ";base64," in text:
            return text.split(";base64,", 1)[1].strip()
        if text and len(text) > 60:
            try:
                base64.b64decode(text, validate=True)
                return text
            except Exception:
                return None
        return None
    if isinstance(node, dict):
        for key in ("base64", "data", "media", "file", "content"):
            if key in node:
                found = _find_base64_candidate(node.get(key))
                if found:
                    return found
        for value in node.values():
            found = _find_base64_candidate(value)
            if found:
                return found
    if isinstance(node, list):
        for item in node:
            found = _find_base64_candidate(item)
            if found:
                return found
    return None


async def get_base64_from_media_message(
    instance_name: str,
    *,
    message_key: dict[str, Any],
    message_object: dict[str, Any] | None = None,
    convert_to_mp4: bool = False,
) -> str:
    if not isinstance(message_key, dict) or not str(message_key.get("id") or "").strip():
        raise EvolutionError(
            message="No se pudo descifrar media: falta message.key.id",
            status_code=422,
            retryable=False,
        )

    body: dict[str, Any] = {
        "message": {
            "key": message_key,
        },
        "convertToMp4": bool(convert_to_mp4),
    }
    if isinstance(message_object, dict) and message_object:
        body["message"]["message"] = message_object

    result = await _request(
        "POST",
        f"/chat/getBase64FromMediaMessage/{instance_name}",
        json=body,
        retries=0,
    )
    candidate = _find_base64_candidate(result)
    if not candidate:
        raise EvolutionError(
            message="Evolution no devolvio base64 para media message",
            status_code=502,
            detail={"endpoint": f"/chat/getBase64FromMediaMessage/{instance_name}", "response_type": type(result).__name__},
            retryable=False,
        )
    return candidate
