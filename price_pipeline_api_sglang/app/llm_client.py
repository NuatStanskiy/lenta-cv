from __future__ import annotations

import base64
from typing import Any

import requests

from .config import Settings
from .image_ops import encode_jpeg
from .postprocess import extract_json_object, postprocess
from .prompt import PROMPT
from .schemas import CropCandidate, JsonDict

_LLM_FIELDS = [
    "product_name",
    "price_default",
    "price_card",
    "price_discount",
    "barcode",
    "discount_amount",
    "id_sku",
    "print_datetime",
    "code",
    "additional_info",
    "color",
    "special_symbols",
]

PRICE_TAG_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in _LLM_FIELDS},
    "required": list(_LLM_FIELDS),
    "additionalProperties": False,
}


class LlmClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = settings.llm_api_url + settings.llm_endpoint
        self.session = requests.Session()

    def extract(self, crop: CropCandidate) -> JsonDict:
        if self.settings.llm_backend == "legacy_extract":
            return self._extract_legacy(crop)
        return self._extract_openai_vision(crop)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.settings.llm_api_key:
            # SGLang usually does not require auth locally, but this is useful behind a proxy.
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
            headers["X-API-Key"] = self.settings.llm_api_key
        return headers

    def _extract_legacy(self, crop: CropCandidate) -> JsonDict:
        files = {
            "image": (f"{crop.crop_id}.jpg", encode_jpeg(crop.image_bgr), "image/jpeg"),
        }
        resp = self.session.post(self.url, files=files, headers=self._headers(), timeout=self.settings.llm_timeout_sec)
        resp.raise_for_status()
        payload = resp.json()

        if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
            return payload
        return {"ok": True, "result": payload}

    def _extract_openai_vision(self, crop: CropCandidate) -> JsonDict:
        image_bytes = encode_jpeg(crop.image_bgr)
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        body: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                }
            ],
            "temperature": self.settings.llm_temperature,
            "top_p": self.settings.llm_top_p,
            "max_tokens": self.settings.llm_max_tokens,
            "chat_template_kwargs": {"enable_thinking": self.settings.llm_enable_thinking},
        }

        if self.settings.llm_use_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "price_tag_ocr",
                    "schema": PRICE_TAG_JSON_SCHEMA,
                },
            }

        resp = self.session.post(
            self.url,
            json=body,
            headers={"Content-Type": "application/json", **self._headers()},
            timeout=self.settings.llm_timeout_sec,
        )
        resp.raise_for_status()
        payload = resp.json()

        try:
            message = payload["choices"][0]["message"]
            raw_text = message.get("content") or ""
            data = extract_json_object(raw_text)
            result = postprocess(data)
            return {
                "ok": True,
                "result": result,
                "raw_model_output": raw_text,
                "llm_backend": "openai_vision",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Failed to parse OpenAI-compatible LLM response: {exc!r}",
                "result": {},
                "raw_model_output": payload,
                "llm_backend": "openai_vision",
            }
