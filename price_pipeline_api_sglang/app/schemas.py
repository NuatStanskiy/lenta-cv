from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(slots=True)
class CropCandidate:
    crop_id: str
    image_bgr: np.ndarray
    bbox_xyxy: tuple[int, int, int, int]
    confidence: float | None
    frame_index: int
    time_sec: float | None
    track_id: str | None = None
    sharpness: float | None = None


JsonDict = dict[str, Any]
