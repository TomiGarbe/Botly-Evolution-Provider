from __future__ import annotations

import mimetypes
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.media import cleanup_cache, get_uploaded_file, save_uploaded_file, stream_from_url
from app.services.normalization import get_media

router = APIRouter(tags=["media"])
logger = get_logger(__name__)

_ALLOWED_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "text/",
    "application/vnd.",
)

_ALLOWED_EXACT_MIMES = {
    "application/pdf",
    "application/msword",
    "application/rtf",
    "application/zip",
    "application/x-zip-compressed",
    "application/json",
    "application/xml",
    "text/xml",
    "application/octet-stream",
}

_KNOWN_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
    ".zip",
    ".json",
    ".xml",
    ".rtf",
}


def _resolve_effective_mime(content_type: str | None, filename: str | None) -> str:
    raw = (content_type or "").split(";", 1)[0].strip().lower()
    if raw and raw != "application/octet-stream":
        return raw

    guessed, _ = mimetypes.guess_type(filename or "")
    guessed_norm = (guessed or "").split(";", 1)[0].strip().lower()
    if guessed_norm:
        return guessed_norm

    return raw or "application/octet-stream"


def _allowed_mime(effective_mime: str, filename: str | None) -> bool:
    settings = get_settings()
    allowed = [item.strip() for item in settings.media_allowed_mime_prefixes.split(",") if item.strip()]
    ext = Path(filename or "").suffix.lower()

    if effective_mime in _ALLOWED_EXACT_MIMES:
        return True

    if any(effective_mime.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        return True

    # Backward-compat: si el deploy define lista custom, la respetamos.
    if any(effective_mime.startswith(prefix.lower()) for prefix in allowed):
        return True

    # Fallback pragmatico para browsers que envian octet-stream o MIME vacio.
    if ext in _KNOWN_DOCUMENT_EXTENSIONS:
        return True

    return False


@router.get("/instances/{instance_name}/media/{media_id}")
async def proxy_media(instance_name: str, media_id: str):
    metadata = get_media(media_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Media no encontrada en cache de eventos")
    if metadata.get("instance") != instance_name:
        raise HTTPException(status_code=403, detail="Media no pertenece a la instancia")
    source_url = str(metadata.get("url") or "")
    if not source_url:
        raise HTTPException(status_code=404, detail="Media sin URL")

    try:
        stream, headers = await stream_from_url(source_url, use_cache=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo descargar media: {exc}")
    response_headers = {"X-Media-Source": "evolution"}
    response_headers.update(headers)
    media_type = str(metadata.get("mimeType") or "application/octet-stream")
    return StreamingResponse(stream, media_type=media_type, headers=response_headers)


@router.get("/media/fetch")
async def fetch_by_url(url: str = Query(...), mime_type: str | None = Query(default=None)):
    try:
        stream, headers = await stream_from_url(url, use_cache=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo descargar media: {exc}")
    response_headers = {"X-Media-Source": "direct-url"}
    response_headers.update(headers)
    return StreamingResponse(stream, media_type=mime_type or "application/octet-stream", headers=response_headers)


@router.post("/media/upload")
async def upload_media(file: UploadFile = File(...)):
    settings = get_settings()
    max_bytes = settings.media_max_upload_mb * 1024 * 1024
    file_name = file.filename or "file.bin"
    incoming_mime = (file.content_type or "").strip().lower()
    extension = Path(file_name).suffix.lower()
    effective_mime = _resolve_effective_mime(file.content_type, file_name)

    allowed = _allowed_mime(effective_mime, file_name)
    if not allowed:
        logger.warning(
            "media_upload_rejected",
            fileName=file_name,
            mimeType=incoming_mime or "empty",
            inferredMime=effective_mime,
            extension=extension or "none",
            reason="mime_not_allowed",
        )
        raise HTTPException(status_code=415, detail=f"Tipo de archivo no permitido: {incoming_mime or effective_mime}")

    logger.info(
        "media_upload_validation",
        fileName=file_name,
        mimeType=incoming_mime or "empty",
        inferredMime=effective_mime,
        extension=extension or "none",
        allowed=allowed,
    )

    size = 0
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"Archivo excede {settings.media_max_upload_mb}MB")
            tmp.write(chunk)

    saved = save_uploaded_file(file_name, effective_mime, size, tmp_path)
    await cleanup_cache()
    return {"file": saved}


@router.get("/media/upload/{file_id}")
async def get_uploaded(file_id: str):
    data = get_uploaded_file(file_id)
    if not data:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    path = Path(str(data["path"]))
    guessed, _ = mimetypes.guess_type(path.name)
    return FileResponse(
        path=str(path),
        media_type=str(data.get("contentType") or guessed or "application/octet-stream"),
        filename=str(data.get("fileName") or path.name),
    )
