from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

import numpy as np
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config for the remote detection/crop/orchestration API."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "price-pipeline-api"
    api_key: str | None = Field(default=None, description="Optional incoming API key checked via X-API-Key")

    model_path: str = Field(default="/models/price-tag-detection.pt")
    device: str = Field(default="cpu", description="cpu, cuda, cuda:0, etc.")
    conf_threshold: float = 0.30
    iou_threshold: float = 0.50
    crop_padding: float = 0.10
    edge_margin: int = 15
    max_detections_per_frame: int = 100

    frame_interval_sec: float = 0.20
    top_k_per_track: int = 1
    enable_tracking: bool = True
    tracker_type: Literal["botsort", "bytetrack"] = "botsort"
    track_high_thresh: float = 0.50
    track_low_thresh: float = 0.10
    new_track_thresh: float = 0.60
    track_buffer: int = 30
    match_thresh: float = 0.80
    gmc_method: str = "sparseOptFlow"

    rotate: Literal["none", "cw", "ccw", "180"] = Field(default="ccw")

    fisheye_enabled: bool = False
    fisheye_k: str | None = Field(default=None, description="JSON 3x3 camera matrix")
    fisheye_d: str | None = Field(default=None, description="JSON distortion vector, 4 elems")
    fisheye_dim: str | None = Field(default=None, description="JSON [width,height] for calibration image size")
    fisheye_balance: float = 0.0
    fisheye_fov_scale: float = 1.0

    # LLM backend.
    # legacy_extract: old custom /extract API that accepts multipart image.
    # openai_vision: SGLang/vLLM/OpenAI-compatible /v1/chat/completions API.
    llm_backend: Literal["legacy_extract", "openai_vision"] = "openai_vision"
    llm_api_url: str = Field(default="http://127.0.0.1:30000")
    llm_api_key: str | None = None
    llm_timeout_sec: float = 180.0
    llm_endpoint: str = "/v1/chat/completions"
    llm_model: str = "Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ"
    llm_max_tokens: int = 768
    llm_temperature: float = 0.0
    llm_top_p: float = 0.95
    llm_use_json_schema: bool = True
    llm_enable_thinking: bool = False

    save_debug_crops: bool = False
    debug_dir: str = "/tmp/price_pipeline_debug"

    @field_validator("llm_api_url")
    @classmethod
    def strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def camera_matrix(self) -> np.ndarray | None:
        if not self.fisheye_k:
            return None
        return np.array(json.loads(self.fisheye_k), dtype=np.float64)

    def distortion_coeffs(self) -> np.ndarray | None:
        if not self.fisheye_d:
            return None
        return np.array(json.loads(self.fisheye_d), dtype=np.float64).reshape(-1, 1)

    def calibration_dim(self) -> tuple[int, int] | None:
        if not self.fisheye_dim:
            return None
        w, h = json.loads(self.fisheye_dim)
        return int(w), int(h)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
