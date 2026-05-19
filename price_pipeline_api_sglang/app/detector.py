from __future__ import annotations

import heapq
import os
import tempfile
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from .config import Settings
from .fisheye import FisheyeCorrector
from .image_ops import add_padding, is_clipped, laplacian_score
from .schemas import CropCandidate


class PriceTagDetector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not Path(settings.model_path).exists():
            raise FileNotFoundError(
                f"YOLO weights not found: {settings.model_path}. "
                "Mount your price-tag-detection .pt file and set MODEL_PATH."
            )
        self.model = YOLO(settings.model_path)
        self.corrector = FisheyeCorrector(settings)
        self._tracker_config_path = self._write_tracker_config()

    def process_image(self, image_bgr: np.ndarray) -> list[CropCandidate]:
        self.model.predictor = None  # drop leftover tracker state from any prior video run
        frame = self.corrector.process(image_bgr)
        return self._predict_frame(frame, frame_index=0, time_sec=0.0, prefix="img")

    def process_video_file(self, video_path: str) -> list[CropCandidate]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        frame_step = max(1, int(round(fps * self.settings.frame_interval_sec)))
        tracks: dict[str, list[tuple[float, int, str, CropCandidate]]] = defaultdict(list)
        fallback: list[CropCandidate] = []

        frame_index = 0
        self.model.predictor = None

        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                if frame_index % frame_step != 0:
                    frame_index += 1
                    continue

                processed = self.corrector.process(frame)
                time_sec = frame_index / fps if fps > 0 else None

                if self.settings.enable_tracking:
                    candidates = self._track_frame(processed, frame_index, time_sec)
                else:
                    candidates = self._predict_frame(processed, frame_index, time_sec, prefix="video")

                for cand in candidates:
                    if cand.track_id is None:
                        fallback.append(cand)
                        continue
                    heap = tracks[cand.track_id]
                    # heapq compares tuple elems; include frame_index to keep ordering deterministic.
                    item = (float(cand.sharpness or 0.0), int(cand.frame_index), cand.crop_id, cand)
                    if len(heap) < self.settings.top_k_per_track:
                        heapq.heappush(heap, item)
                    elif item[0] > heap[0][0]:
                        heapq.heapreplace(heap, item)

                frame_index += 1
        finally:
            cap.release()

        best: list[CropCandidate] = []
        for heap in tracks.values():
            best.extend([item[3] for item in sorted(heap, reverse=True)])
        best.extend(fallback)
        return best

    def _predict_frame(self, frame_bgr: np.ndarray, frame_index: int, time_sec: float | None, prefix: str) -> list[CropCandidate]:
        h, w = frame_bgr.shape[:2]
        result = self.model.predict(
            source=frame_bgr,
            conf=self.settings.conf_threshold,
            iou=self.settings.iou_threshold,
            device=self.settings.device,
            max_det=self.settings.max_detections_per_frame,
            verbose=False,
        )[0]
        out: list[CropCandidate] = []
        if result.boxes is None:
            return out

        boxes = result.boxes.xyxy.cpu().numpy().astype(int)
        confs = result.boxes.conf.cpu().numpy() if result.boxes.conf is not None else [None] * len(boxes)
        for idx, (box, conf) in enumerate(zip(boxes, confs)):
            cand = self._candidate_from_box(frame_bgr, tuple(map(int, box)), frame_index, time_sec, str(idx), prefix, None, float(conf))
            if cand:
                out.append(cand)
        return out

    def _track_frame(self, frame_bgr: np.ndarray, frame_index: int, time_sec: float | None) -> list[CropCandidate]:
        h, w = frame_bgr.shape[:2]
        result = self.model.track(
            source=frame_bgr,
            conf=self.settings.conf_threshold,
            iou=self.settings.iou_threshold,
            device=self.settings.device,
            tracker=self._tracker_config_path,
            persist=True,
            max_det=self.settings.max_detections_per_frame,
            verbose=False,
        )[0]
        out: list[CropCandidate] = []
        if result.boxes is None:
            return out

        boxes = result.boxes.xyxy.cpu().numpy().astype(int)
        confs = result.boxes.conf.cpu().numpy() if result.boxes.conf is not None else [None] * len(boxes)
        ids = result.boxes.id.cpu().numpy().astype(int) if result.boxes.id is not None else [None] * len(boxes)

        for idx, (box, track_id, conf) in enumerate(zip(boxes, ids, confs)):
            track_str = str(track_id) if track_id is not None else None
            suffix = track_str or str(idx)
            cand = self._candidate_from_box(frame_bgr, tuple(map(int, box)), frame_index, time_sec, suffix, "track", track_str, float(conf))
            if cand:
                out.append(cand)
        return out

    def _candidate_from_box(
        self,
        frame_bgr: np.ndarray,
        box_xyxy: tuple[int, int, int, int],
        frame_index: int,
        time_sec: float | None,
        suffix: str,
        prefix: str,
        track_id: str | None,
        confidence: float | None,
    ) -> CropCandidate | None:
        h, w = frame_bgr.shape[:2]
        x1, y1, x2, y2 = box_xyxy
        if x2 <= x1 or y2 <= y1:
            return None
        if is_clipped(box_xyxy, w, h, self.settings.edge_margin):
            return None

        px1, py1, px2, py2 = add_padding(box_xyxy, w, h, self.settings.crop_padding)
        crop = frame_bgr[py1:py2, px1:px2]
        if crop.size == 0:
            return None
        sharpness = laplacian_score(crop)
        crop_id = f"{prefix}_{suffix}_frame_{frame_index}"
        return CropCandidate(
            crop_id=crop_id,
            image_bgr=crop.copy(),
            bbox_xyxy=(px1, py1, px2, py2),
            confidence=confidence,
            frame_index=frame_index,
            time_sec=time_sec,
            track_id=track_id,
            sharpness=sharpness,
        )

    def _write_tracker_config(self) -> str:
        tracker_type = self.settings.tracker_type
        if tracker_type == "bytetrack":
            body = f"""tracker_type: bytetrack
track_high_thresh: {self.settings.track_high_thresh}
track_low_thresh: {self.settings.track_low_thresh}
new_track_thresh: {self.settings.new_track_thresh}
track_buffer: {self.settings.track_buffer}
match_thresh: {self.settings.match_thresh}
fuse_score: True
"""
        else:
            body = f"""tracker_type: botsort
track_high_thresh: {self.settings.track_high_thresh}
track_low_thresh: {self.settings.track_low_thresh}
new_track_thresh: {self.settings.new_track_thresh}
track_buffer: {self.settings.track_buffer}
match_thresh: {self.settings.match_thresh}
fuse_score: True
gmc_method: {self.settings.gmc_method}
with_reid: False
proximity_thresh: 0.5
appearance_thresh: 0.25
"""
        fd, path = tempfile.mkstemp(prefix=f"{tracker_type}_", suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        return path
