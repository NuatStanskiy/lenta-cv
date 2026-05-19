from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
from fastapi import UploadFile

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}


async def persist_upload(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "upload.bin").suffix.lower() or ".bin"
    fd, path = tempfile.mkstemp(prefix="upload_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return path
    except Exception:
        try:
            os.unlink(path)
        finally:
            raise


def media_kind(path: str, content_type: str | None = None) -> str:
    ext = Path(path).suffix.lower()
    ct = (content_type or "").lower()
    if ext in IMAGE_EXTS or ct.startswith("image/"):
        return "image"
    if ext in VIDEO_EXTS or ct.startswith("video/"):
        return "video"
    raise ValueError(f"Unsupported media type: ext={ext!r}, content_type={content_type!r}")


def read_image_bgr(path: str) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not decode image: {path}")
    return img
