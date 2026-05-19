from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import Settings


@dataclass
class FisheyeCorrector:
    """Optional fisheye undistortion + fixed orientation.

    If fisheye parameters are absent, this intentionally becomes a no-op for undistortion.
    Rotation still applies because the source notebooks rotated frames CCW before detection.
    """

    settings: Settings
    _map_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)

    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        frame_bgr = self._rotate(frame_bgr)
        if not self.settings.fisheye_enabled:
            return frame_bgr
        k = self.settings.camera_matrix()
        d = self.settings.distortion_coeffs()
        if k is None or d is None:
            return frame_bgr
        return self._undistort(frame_bgr, k, d)

    def _rotate(self, frame_bgr: np.ndarray) -> np.ndarray:
        mode = self.settings.rotate
        if mode == "ccw":
            return cv2.rotate(frame_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if mode == "cw":
            return cv2.rotate(frame_bgr, cv2.ROTATE_90_CLOCKWISE)
        if mode == "180":
            return cv2.rotate(frame_bgr, cv2.ROTATE_180)
        return frame_bgr

    def _undistort(self, frame_bgr: np.ndarray, k: np.ndarray, d: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        key = (w, h)
        if key not in self._map_cache:
            k_scaled = k.copy()
            calib_dim = self.settings.calibration_dim()
            if calib_dim and calib_dim != key:
                calib_w, calib_h = calib_dim
                k_scaled[0, :] *= w / calib_w
                k_scaled[1, :] *= h / calib_h

            new_k = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
                k_scaled,
                d,
                (w, h),
                np.eye(3),
                balance=float(self.settings.fisheye_balance),
                fov_scale=float(self.settings.fisheye_fov_scale),
            )
            map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                k_scaled,
                d,
                np.eye(3),
                new_k,
                (w, h),
                cv2.CV_16SC2,
            )
            self._map_cache[key] = (map1, map2)
        map1, map2 = self._map_cache[key]
        return cv2.remap(frame_bgr, map1, map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
