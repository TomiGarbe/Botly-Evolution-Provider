from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
import uuid
from io import StringIO
from pathlib import Path
from typing import AsyncIterator

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

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


async def stream_from_url(url: str, *, use_cache: bool = True) -> tuple[AsyncIterator[bytes], dict[str, str]]:
    settings = get_settings()
    cache_path = _cache_path(url)

    if use_cache and _is_fresh(cache_path):
        logger.info("media_cache_hit", file=str(cache_path))

        async def read_file() -> AsyncIterator[bytes]:
            with cache_path.open("rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        headers = {"X-Media-Cache": "HIT", "Content-Length": str(cache_path.stat().st_size)}
        return read_file(), headers

    timeout = httpx.Timeout(connect=8.0, read=settings.media_download_timeout, write=15.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def iterator() -> AsyncIterator[bytes]:
        tmp_file = None
        file_obj = None
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                if use_cache:
                    tmp_file = cache_path.with_suffix(".tmp")
                    fd = os.open(tmp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                    file_obj = os.fdopen(fd, "wb")
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if file_obj is not None:
                        file_obj.write(chunk)
                    yield chunk
                if file_obj is not None:
                    file_obj.flush()
                    file_obj.close()
                    os.replace(tmp_file, cache_path)
        finally:
            if file_obj is not None and not file_obj.closed:
                file_obj.close()
            await client.aclose()

    headers = {
        "X-Media-Cache": "MISS",
    }
    return iterator(), headers
