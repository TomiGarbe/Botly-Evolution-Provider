import time
import uuid
import base64
from io import StringIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.requests import (
    SendTextRequest,
    SendMediaRequest,
    SendButtonsRequest,
    SendListRequest,
    CheckNumbersRequest,
    SendUploadedMediaRequest,
    SendMessageRequest,
)
from app.services import evolution
from app.services.media import consume_uploaded_file, file_to_base64, get_uploaded_file
from app.services.normalization import save_business_event, save_pipeline_event
from app.services.reliability import mark_outbound

logger = get_logger(__name__)
router = APIRouter(tags=["messages"])
_MEDIA_TYPES = {"image", "audio", "video", "document", "file", "pdf"}


def _normalize_media_type(raw: str, mime_type: str) -> str:
    value = (raw or "").strip().lower()
    if value in ("image", "audio", "video", "document"):
        return value
    if value == "pdf":
        return "document"
    if value == "file":
        if mime_type == "application/pdf":
            return "document"
        return "document"
    return "document"


def _clean_number(raw: Any) -> str:
    return "".join(ch for ch in str(raw or "") if ch.isdigit())


def _normalize_message_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"text", "image", "audio", "video", "document", "file", "pdf"}:
        return value
    return ""


def _validate_instance_name(raw: str) -> str:
    value = str(raw or "").strip()
    if not value or not all(ch.islower() or ch.isdigit() or ch == "_" for ch in value):
        logger.warning("invalid_instance", instance=value or None)
        raise HTTPException(status_code=400, detail="Nombre de instancia invalido")
    return value


async def _upload_file_to_base64(file: UploadFile, max_bytes: int) -> tuple[str, int]:
    size = 0
    encoded = StringIO()
    try:
        while True:
            chunk = await file.read(57 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError(f"Archivo excede {max_bytes} bytes")
            encoded.write(base64.b64encode(chunk).decode("ascii"))
    finally:
        await file.close()
    if size <= 0:
        raise ValueError("Archivo vacio")
    return encoded.getvalue(), size


def _validate_message_payload(payload: SendMessageRequest) -> tuple[str, str]:
    msg_type = _normalize_message_type(payload.type)
    number = _clean_number(payload.number)
    if len(number) < 8:
        raise HTTPException(status_code=422, detail="number invalido")

    if msg_type == "text":
        if not (payload.text or "").strip():
            raise HTTPException(status_code=422, detail="text es obligatorio para type=text")
    elif msg_type in _MEDIA_TYPES:
        if not any([(payload.mediaUrl or "").strip(), (payload.base64 or "").strip()]):
            raise HTTPException(status_code=422, detail="file/base64/mediaUrl es obligatorio para media")
    else:
        raise HTTPException(status_code=422, detail="type invalido")
    return number, msg_type


def _log_message_start(instance_name: str, msg_type: str, number: str, metadata: dict[str, Any] | None = None) -> None:
    logger.info("message_send_start", instance=instance_name, type=msg_type, number=number, metadata=metadata or {})


def _log_message_success(instance_name: str, msg_type: str, number: str) -> None:
    logger.info("message_send_success", instance=instance_name, type=msg_type, number=number)


async def _send_message_unified(instance_name: str, request: Request):
    instance_name = _validate_instance_name(instance_name)
    settings = get_settings()
    content_type = str(request.headers.get("content-type") or "").lower()

    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            msg_type = _normalize_message_type(form.get("type"))
            number = _clean_number(form.get("number"))
            caption = str(form.get("caption") or "")
            text = str(form.get("text") or "")
            file = form.get("file")

            if len(number) < 8 or not msg_type:
                logger.warning("invalid_payload", instance=instance_name, reason="invalid_number_or_type")
                raise HTTPException(status_code=422, detail="Payload invalido")

            if msg_type == "text":
                if not text.strip():
                    logger.warning("invalid_payload", instance=instance_name, reason="missing_text")
                    raise HTTPException(status_code=422, detail="text es obligatorio para type=text")
                _log_message_start(instance_name, "text", number)
                result = await evolution.send_text(instance_name, number, text.strip())
                _log_message_success(instance_name, "text", number)
                return result

            if msg_type not in _MEDIA_TYPES:
                logger.warning("unsupported_media", instance=instance_name, type=msg_type)
                raise HTTPException(status_code=422, detail="type invalido para media")
            if not isinstance(file, UploadFile):
                logger.warning("invalid_payload", instance=instance_name, reason="missing_file")
                raise HTTPException(status_code=422, detail="file es obligatorio para multipart media")

            normalized_media_type = _normalize_media_type(msg_type, (file.content_type or "").lower())
            _log_message_start(instance_name, normalized_media_type, number)
            try:
                media_base64, _ = await _upload_file_to_base64(file, settings.media_max_upload_mb * 1024 * 1024)
            except ValueError as exc:
                logger.error("upload_fail", instance=instance_name, error=str(exc))
                raise HTTPException(status_code=413, detail=str(exc)) from exc

            result = await evolution.send_media(
                instance_name=instance_name,
                number=number,
                media_payload=media_base64,
                mediatype=normalized_media_type,
                mimetype=(file.content_type or "application/octet-stream"),
                file_name=(file.filename or "file.bin"),
                caption=caption.strip(),
            )
            _log_message_success(instance_name, normalized_media_type, number)
            return result

        data = await request.json()
        payload = SendMessageRequest.model_validate(data)
        number, msg_type = _validate_message_payload(payload)

        if msg_type == "text":
            _log_message_start(instance_name, "text", number, payload.metadata)
            result = await evolution.send_text(instance_name, number, (payload.text or "").strip())
            _log_message_success(instance_name, "text", number)
            return result

        if msg_type not in _MEDIA_TYPES:
            logger.warning("unsupported_media", instance=instance_name, type=msg_type)
            raise HTTPException(status_code=422, detail="type invalido para media")

        normalized_media_type = _normalize_media_type(msg_type, "application/octet-stream")
        media_payload = (payload.mediaUrl or "").strip() or (payload.base64 or "").strip()
        _log_message_start(instance_name, normalized_media_type, number, payload.metadata)
        result = await evolution.send_media(
            instance_name=instance_name,
            number=number,
            media_payload=media_payload,
            mediatype=normalized_media_type,
            mimetype="application/octet-stream",
            file_name="file.bin",
            caption=(payload.caption or "").strip(),
        )
        _log_message_success(instance_name, normalized_media_type, number)
        return result
    except HTTPException:
        raise
    except evolution.EvolutionError as exc:
        logger.error("evolution_fail", instance=instance_name, error=str(exc))
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("invalid_payload", instance=instance_name, error=str(exc))
        raise HTTPException(status_code=422, detail=f"Payload invalido: {exc}") from exc


@router.post("/messages/{instance_name}")
async def send_message_public(instance_name: str, request: Request):
    return await _send_message_unified(instance_name, request)


@router.post("/instances/{instance_name}/messages", deprecated=True)
async def send_message_legacy(instance_name: str, request: Request):
    logger.warning("deprecated_endpoint_usage", endpoint="/instances/{instance_name}/messages", replacement="/messages/{instance_name}")
    return await _send_message_unified(instance_name, request)


@router.post("/instances/{instance_name}/messages/text", deprecated=True)
async def send_text(instance_name: str, body: SendTextRequest):
    payload_fp = mark_outbound(instance_name, body.number, "text", body.text)
    save_pipeline_event(
        stage="send_whatsapp",
        status="attempt",
        instance=instance_name,
        details={"kind": "text", "number": body.number, "outboundFingerprint": payload_fp},
    )
    try:
        result = await evolution.send_text(instance_name, body.number, body.text)
        save_business_event(
            {
                "id": str(uuid.uuid4())[:16],
                "layer": "business",
                "event": "LOCAL_TEXT_SEND",
                "instance": instance_name,
                "timestamp": int(time.time() * 1000),
                "direction": "outbound",
                "type": "message",
                "messageType": "text",
                "sender": instance_name,
                "recipient": body.number,
                "content": body.text,
                "text": body.text,
                "status": "sent",
                "fromMe": True,
                "fromBot": False,
                "forwarding": {"status": "local_api_send"},
                "error": None,
                "message": {"id": None, "from": instance_name, "kind": "text", "text": body.text},
                "media": None,
            }
        )
        logger.info("text_message_outgoing_sent", instance=instance_name, recipient=body.number)
        save_pipeline_event(stage="send_whatsapp", status="ok", instance=instance_name, details={"kind": "text"})
        return result
    except Exception as exc:
        save_pipeline_event(
            stage="send_whatsapp",
            status="failed",
            instance=instance_name,
            details={"kind": "text", "error": str(exc)[:180]},
        )
        logger.error("send_text_failed", instance=instance_name, error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/instances/{instance_name}/messages/media", deprecated=True)
async def send_media(instance_name: str, body: SendMediaRequest):
    payload_fp = mark_outbound(
        instance_name, body.number, body.mediatype, f"{body.caption or ''}|{body.media_url or ''}"
    )
    save_pipeline_event(
        stage="send_whatsapp",
        status="attempt",
        instance=instance_name,
        details={"kind": body.mediatype, "number": body.number, "outboundFingerprint": payload_fp},
    )
    try:
        result = await evolution.send_media(
            instance_name, body.number, body.media_url, body.mediatype, body.mimetype, body.file_name, body.caption
        )
        save_business_event(
            {
                "id": str(uuid.uuid4())[:16],
                "layer": "business",
                "event": "LOCAL_MEDIA_SEND",
                "instance": instance_name,
                "timestamp": int(time.time() * 1000),
                "direction": "outbound",
                "type": "message",
                "messageType": body.mediatype,
                "sender": instance_name,
                "recipient": body.number,
                "content": body.caption or body.media_url,
                "text": body.caption,
                "status": "sent",
                "fromMe": True,
                "fromBot": False,
                "forwarding": {"status": "local_api_send"},
                "error": None,
                "message": {"id": None, "from": instance_name, "kind": body.mediatype, "text": body.caption},
                "media": {"id": str(uuid.uuid4())[:16], "kind": body.mediatype, "caption": body.caption, "url": body.media_url},
            }
        )
        save_pipeline_event(stage="send_whatsapp", status="ok", instance=instance_name, details={"kind": body.mediatype})
        return result
    except Exception as exc:
        save_pipeline_event(
            stage="send_whatsapp",
            status="failed",
            instance=instance_name,
            details={"kind": body.mediatype, "error": str(exc)[:180]},
        )
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/instances/{instance_name}/messages/media/uploaded", deprecated=True)
async def send_uploaded_media(instance_name: str, body: SendUploadedMediaRequest):
    file_data = get_uploaded_file(body.file_id)
    if not file_data:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    settings = get_settings()
    file_name = str(file_data.get("fileName") or "file.bin")
    mime_type = str(file_data.get("contentType") or "application/octet-stream")
    normalized_media_type = _normalize_media_type(body.mediatype, mime_type)
    file_size = int(file_data.get("size") or 0)
    max_bytes = settings.media_max_upload_mb * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(status_code=413, detail=f"Archivo excede {settings.media_max_upload_mb}MB")

    path = Path(str(file_data.get("path") or ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")

    logger.info(
        "media_send_start",
        instance=instance_name,
        fileId=body.file_id,
        mediatype=normalized_media_type,
        mimeType=mime_type,
        fileName=file_name,
        size=file_size,
    )
    try:
        media_base64, actual_size = file_to_base64(path, max_bytes)
        if settings.debug:
            logger.info(
                "media_send_payload_debug",
                endpoint=f"/message/sendMedia/{instance_name}",
                mediatype=normalized_media_type,
                mimetype=mime_type,
                fileName=file_name,
                caption=body.caption,
                mediaPreview=media_base64[:48] + "...",
                size=actual_size,
            )
        payload_fp = mark_outbound(
            instance_name, body.number, normalized_media_type, f"{body.caption or ''}|uploaded:{body.file_id}"
        )
        save_pipeline_event(
            stage="send_whatsapp",
            status="attempt",
            instance=instance_name,
            details={"kind": normalized_media_type, "number": body.number, "outboundFingerprint": payload_fp},
        )
        result = await evolution.send_media(
            instance_name=instance_name,
            number=body.number,
            media_payload=media_base64,
            mediatype=normalized_media_type,
            mimetype=mime_type,
            file_name=file_name,
            caption=body.caption,
        )
        consume_uploaded_file(body.file_id)
        save_business_event(
            {
                "id": str(uuid.uuid4())[:16],
                "layer": "business",
                "event": "LOCAL_MEDIA_SEND",
                "instance": instance_name,
                "timestamp": int(time.time() * 1000),
                "direction": "outbound",
                "type": "message",
                "messageType": normalized_media_type,
                "sender": instance_name,
                "recipient": body.number,
                "content": body.caption or f"uploaded:{body.file_id}",
                "text": body.caption,
                "status": "sent",
                "fromMe": True,
                "fromBot": False,
                "forwarding": {"status": "local_api_send"},
                "error": None,
                "message": {"id": None, "from": instance_name, "kind": normalized_media_type, "text": body.caption},
                "media": {"id": body.file_id, "kind": normalized_media_type, "caption": body.caption, "mimeType": mime_type, "fileName": file_name},
            }
        )
        save_pipeline_event(stage="send_whatsapp", status="ok", instance=instance_name, details={"kind": normalized_media_type})
        logger.info(
            "media_send_success",
            instance=instance_name,
            fileId=body.file_id,
            mediatype=normalized_media_type,
            mimeType=mime_type,
            size=file_size,
        )
        return result
    except ValueError as exc:
        logger.warning(
            "media_send_fail",
            instance=instance_name,
            fileId=body.file_id,
            mediatype=normalized_media_type,
            mimeType=mime_type,
            size=file_size,
            error=str(exc),
        )
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception as exc:
        save_pipeline_event(
            stage="send_whatsapp",
            status="failed",
            instance=instance_name,
            details={"kind": normalized_media_type, "error": str(exc)[:180]},
        )
        logger.warning(
            "media_send_fail",
            instance=instance_name,
            fileId=body.file_id,
            mediatype=normalized_media_type,
            mimeType=mime_type,
            size=file_size,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/instances/{instance_name}/messages/buttons")
async def send_buttons(instance_name: str, body: SendButtonsRequest):
    payload_fp = mark_outbound(instance_name, body.number, "buttons", body.title or body.description or "")
    save_pipeline_event(
        stage="send_whatsapp",
        status="attempt",
        instance=instance_name,
        details={"kind": "buttons", "number": body.number, "outboundFingerprint": payload_fp},
    )
    payload = {
        "number": body.number,
        "title": body.title,
        "description": body.description,
        "footer": body.footer,
        "buttons": [
            {"type": "reply", "displayText": b.display_text, "id": b.id}
            for b in body.buttons
        ],
    }
    try:
        result = await evolution.send_buttons(instance_name, payload)
        save_pipeline_event(stage="send_whatsapp", status="ok", instance=instance_name, details={"kind": "buttons"})
        return result
    except Exception as exc:
        save_pipeline_event(
            stage="send_whatsapp",
            status="failed",
            instance=instance_name,
            details={"kind": "buttons", "error": str(exc)[:180]},
        )
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/instances/{instance_name}/messages/list")
async def send_list(instance_name: str, body: SendListRequest):
    payload_fp = mark_outbound(instance_name, body.number, "list", body.title or body.description or "")
    save_pipeline_event(
        stage="send_whatsapp",
        status="attempt",
        instance=instance_name,
        details={"kind": "list", "number": body.number, "outboundFingerprint": payload_fp},
    )
    payload = {
        "number": body.number,
        "title": body.title,
        "description": body.description,
        "buttonText": body.button_text,
        "footerText": body.footer_text,
        "sections": [
            {
                "title": s.title,
                "rows": [
                    {"title": r.title, "description": r.description, "rowId": r.row_id}
                    for r in s.rows
                ],
            }
            for s in body.sections
        ],
    }
    try:
        result = await evolution.send_list(instance_name, payload)
        save_pipeline_event(stage="send_whatsapp", status="ok", instance=instance_name, details={"kind": "list"})
        return result
    except Exception as exc:
        save_pipeline_event(
            stage="send_whatsapp",
            status="failed",
            instance=instance_name,
            details={"kind": "list", "error": str(exc)[:180]},
        )
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/instances/{instance_name}/messages/check-numbers")
async def check_numbers(instance_name: str, body: CheckNumbersRequest):
    try:
        return await evolution.check_whatsapp_numbers(instance_name, body.numbers)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
