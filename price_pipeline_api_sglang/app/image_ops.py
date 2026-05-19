from __future__ import annotations

import cv2
import numpy as np


def laplacian_score(img_bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def add_padding(
    box_xyxy: tuple[int, int, int, int],
    img_w: int,
    img_h: int,
    padding: float = 0.10,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box_xyxy
    bw = max(1, x2 - x1)
    bh = max(1, y2 - y1)
    pad_x = int(bw * padding)
    pad_y = int(bh * padding)
    return (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(img_w, x2 + pad_x),
        min(img_h, y2 + pad_y),
    )


def is_clipped(box_xyxy: tuple[int, int, int, int], img_w: int, img_h: int, edge_margin: int) -> bool:
    x1, y1, x2, y2 = box_xyxy
    return x1 < edge_margin or y1 < edge_margin or x2 > img_w - edge_margin or y2 > img_h - edge_margin


def encode_jpeg(img_bgr: np.ndarray, quality: int = 95) -> bytes:
    ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("Could not JPEG-encode crop")
    return bytes(buf)
