from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
import uuid
from io import StringIO
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import evolution

logger = get_logger(__name__)
_cleanup_lock = asyncio.Lock()
_uploaded_files: dict[str, dict[str, str | int]] = {}


def _cache_dir() -> Path:
    path = Path(get_settings().media_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _upload_dir() -> Path:
    path = _cache_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_path(url: str) -> Path:
    return _cache_dir() / f"{_cache_key(url)}.bin"


def _decrypted_cache_path(instance: str, media_id: str) -> Path:
    key = hashlib.sha256(f"{instance}:{media_id}".encode("utf-8")).hexdigest()
    return _cache_dir() / f"dec_{key}.bin"


def _is_fresh(path: Path) -> bool:
    ttl = get_settings().media_cache_ttl_seconds
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl


async def cleanup_cache() -> None:
    async with _cleanup_lock:
        settings = get_settings()
        files = sorted(_cache_dir().glob("*.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
        keep = files[: settings.media_cache_max_files]
        stale = [
            p for p in files[settings.media_cache_max_files :] if (time.time() - p.stat().st_mtime) > settings.media_cache_ttl_seconds
        ]
        for path in stale:
            try:
                path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("media_cache_cleanup_failed", file=str(path), error=str(exc))
        logger.info("media_cache_cleanup", kept=len(keep), removed=len(stale))


def save_uploaded_file(filename: str, content_type: str | None, size: int, temp_path: Path) -> dict[str, str | int]:
    file_id = uuid.uuid4().hex
    ext = "".join(Path(filename or "file.bin").suffixes) or ".bin"
    target_path = _upload_dir() / f"{file_id}{ext}"
    os.replace(temp_path, target_path)
    _uploaded_files[file_id] = {
        "id": file_id,
        "fileName": filename or target_path.name,
        "contentType": content_type or "application/octet-stream",
        "size": size,
        "path": str(target_path),
        "createdAt": int(time.time()),
    }
    return _uploaded_files[file_id]


def get_uploaded_file(file_id: str) -> dict[str, str | int] | None:
    file_data = _uploaded_files.get(file_id)
    if not file_data:
        return None
    path = Path(str(file_data["path"]))
    if not path.exists():
        _uploaded_files.pop(file_id, None)
        return None
    return file_data


def consume_uploaded_file(file_id: str) -> bool:
    file_data = _uploaded_files.pop(file_id, None)
    if not file_data:
        return False
    try:
        Path(str(file_data["path"])).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("uploaded_file_cleanup_failed", file_id=file_id, error=str(exc))
        return False
    return True


def file_to_base64(path: Path, max_bytes: int) -> tuple[str, int]:
    file_size = path.stat().st_size
    if file_size <= 0:
        raise ValueError("Archivo vacio")
    if file_size > max_bytes:
        raise ValueError(f"Archivo excede limite ({max_bytes} bytes)")

    encoded = StringIO()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(57 * 1024)
            if not chunk:
                break
            encoded.write(base64.b64encode(chunk).decode("ascii"))
    return encoded.getvalue(), file_size


def _sniff_magic(payload: bytes) -> str:
    if payload.startswith(b"\xFF\xD8\xFF"):
        return "jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    if payload.startswith(b"%PDF"):
        return "pdf"
    if payload.startswith(b"OggS"):
        return "ogg"
    if len(payload) >= 8 and payload[4:8] == b"ftyp":
        return "mp4"
    return "unknown"


def _validate_magic(payload: bytes, mime_type: str) -> tuple[bool, str]:
    magic = _sniff_magic(payload[:32])
    mime = (mime_type or "").lower()
    if not mime:
        return (magic != "unknown"), magic
    if mime == "image/jpeg":
        return magic == "jpeg", magic
    if mime == "image/png":
        return magic == "png", magic
    if mime == "image/webp":
        return magic == "webp", magic
    if mime == "application/pdf":
        return magic == "pdf", magic
    if mime in {"audio/ogg", "audio/opus", "audio/ogg; codecs=opus"}:
        return magic == "ogg", magic
    if mime.startswith("video/mp4") or mime.startswith("audio/mp4"):
        return magic == "mp4", magic
    if mime.startswith("image/"):
        return magic in {"jpeg", "png", "webp"}, magic
    if mime.startswith("video/"):
        return magic == "mp4", magic
    if mime.startswith("audio/"):
        return magic in {"ogg", "mp4"}, magic
    if mime == "application/octet-stream":
        return magic != "unknown", magic
    return True, magic


async def get_decrypted_media_bytes(metadata: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    from app.services.normalization import update_media_download_state

    instance = str(metadata.get("instance") or "").strip()
    media_id = str(metadata.get("id") or "").strip()
    mime_type = str(metadata.get("mimeType") or "application/octet-stream")
    kind = str(metadata.get("kind") or "unknown")
    if not instance or not media_id:
        raise ValueError("metadata de media incompleta")

    cache_path = _decrypted_cache_path(instance, media_id)
    if _is_fresh(cache_path):
        payload = cache_path.read_bytes()
        valid, magic = _validate_magic(payload, mime_type)
        logger.info(
            "media_download_start",
            instance=instance,
            media_id=media_id,
            media_type=kind,
            mime=mime_type,
            source="cache",
            size=len(payload),
            first_bytes=payload[:24].hex(),
        )
        logger.info("media_magic_bytes", instance=instance, media_id=media_id, mime=mime_type, magic=magic, valid=valid, source="cache")
        if not valid:
            raise ValueError(f"magic bytes invalidos desde cache ({magic})")
        update_media_download_state(media_id, source="cache", decrypted_size=len(payload))
        return payload, {"source": "cache", "magic": magic}

    logger.info(
        "media_download_start",
        instance=instance,
        media_id=media_id,
        media_type=kind,
        mime=mime_type,
        source="evolution_decrypt",
    )
    logger.info("media_decrypt_request", instance=instance, media_id=media_id, media_type=kind, mime=mime_type)
    message_key = metadata.get("messageKey") if isinstance(metadata.get("messageKey"), dict) else {}
    if not message_key:
        message_key = {
            "id": metadata.get("messageId"),
            "remoteJid": metadata.get("remoteJid"),
            "fromMe": metadata.get("fromMe"),
            "participant": metadata.get("participant"),
        }
    message_object = metadata.get("messageObject") if isinstance(metadata.get("messageObject"), dict) else {}
    convert_to_mp4 = kind == "video"
    base64_payload = await evolution.get_base64_from_media_message(
        instance,
        message_key=message_key,
        message_object=message_object,
        convert_to_mp4=convert_to_mp4,
    )
    try:
        payload = base64.b64decode(base64_payload, validate=True)
    except Exception as exc:
        logger.error("media_decrypt_failed", instance=instance, media_id=media_id, reason="invalid_base64", error=str(exc))
        raise ValueError("Evolution devolvio base64 invalido") from exc

    valid, magic = _validate_magic(payload, mime_type)
    logger.info(
        "media_magic_bytes",
        instance=instance,
        media_id=media_id,
        mime=mime_type,
        magic=magic,
        valid=valid,
        source="decrypted",
        size=len(payload),
        first_bytes=payload[:24].hex(),
    )
    if not valid:
        logger.error("media_decrypt_failed", instance=instance, media_id=media_id, reason="magic_mismatch", mime=mime_type, magic=magic)
        raise ValueError(f"magic bytes invalidos para mime {mime_type} ({magic})")

    tmp_path = cache_path.with_suffix(".tmp")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, cache_path)
    logger.info("media_decrypt_success", instance=instance, media_id=media_id, media_type=kind, mime=mime_type, size=len(payload))
    update_media_download_state(media_id, source="decrypted", decrypted_size=len(payload))
    return payload, {"source": "decrypted", "magic": magic}


async def stream_from_url(url: str, *, use_cache: bool = True) -> tuple[AsyncIterator[bytes], dict[str, str]]:
    settings = get_settings()
    cache_path = _cache_path(url)

    if use_cache and _is_fresh(cache_path):
        logger.info("media_cache_hit", file=str(cache_path))

        async def read_file() -> AsyncIterator[bytes]:
            first_bytes = b""
            total = 0
            with cache_path.open("rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    if not first_bytes:
                        first_bytes = chunk[:24]
                    total += len(chunk)
                    yield chunk
            logger.info(
                "media_binary_probe_cache",
                url=url,
                cache_file=str(cache_path),
                bytes_total=total,
                first_bytes_hex=first_bytes.hex(),
            )

        headers = {"X-Media-Cache": "HIT", "Content-Length": str(cache_path.stat().st_size)}
        return read_file(), headers

    timeout = httpx.Timeout(connect=8.0, read=settings.media_download_timeout, write=15.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def iterator() -> AsyncIterator[bytes]:
        tmp_file = None
        file_obj = None
        first_bytes = b""
        total = 0
        remote_headers: dict[str, str] = {}
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                remote_headers = {
                    "content_type": response.headers.get("content-type", ""),
                    "content_length": response.headers.get("content-length", ""),
                    "content_encoding": response.headers.get("content-encoding", ""),
                }
                if use_cache:
                    tmp_file = cache_path.with_suffix(".tmp")
                    fd = os.open(tmp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                    file_obj = os.fdopen(fd, "wb")
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if not first_bytes:
                        first_bytes = chunk[:24]
                    total += len(chunk)
                    if file_obj is not None:
                        file_obj.write(chunk)
                    yield chunk
                if file_obj is not None:
                    file_obj.flush()
                    file_obj.close()
                    os.replace(tmp_file, cache_path)
                logger.info(
                    "media_binary_probe_remote",
                    url=url,
                    bytes_total=total,
                    first_bytes_hex=first_bytes.hex(),
                    remote_content_type=remote_headers.get("content_type") or "",
                    remote_content_length=remote_headers.get("content_length") or "",
                    remote_content_encoding=remote_headers.get("content_encoding") or "",
                    cached=bool(use_cache),
                )
        finally:
            if file_obj is not None and not file_obj.closed:
                file_obj.close()
            await client.aclose()

    headers = {
        "X-Media-Cache": "MISS",
    }
    return iterator(), headers
