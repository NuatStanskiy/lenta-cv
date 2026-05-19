from __future__ import annotations

import base64
import json
import mimetypes
import sys
from pathlib import Path

import requests

PROMPT = """
Ты OCR-система для российских магазинных ценников.
Верни только валидный JSON без markdown.
Извлеки поля:
product_name, type_or_description, country, volume_l, discount_percent,
old_price, new_price, qr_side_code, barcode_digits, raw_visible_text.
Если поле не видно — null. raw_visible_text — массив строк.
""".strip()

SCHEMA = {
    "type": "object",
    "properties": {
        "product_name": {"type": ["string", "null"]},
        "type_or_description": {"type": ["string", "null"]},
        "country": {"type": ["string", "null"]},
        "volume_l": {"type": ["string", "number", "null"]},
        "discount_percent": {"type": ["string", "null"]},
        "old_price": {"type": ["string", "number", "null"]},
        "new_price": {"type": ["string", "number", "null"]},
        "qr_side_code": {"type": ["string", "null"]},
        "barcode_digits": {"type": ["string", "null"]},
        "raw_visible_text": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "product_name", "type_or_description", "country", "volume_l",
        "discount_percent", "old_price", "new_price", "qr_side_code",
        "barcode_digits", "raw_visible_text"
    ],
    "additionalProperties": False,
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python test_sglang_image.py C:\\path\\to\\crop.jpg")

    image_path = Path(sys.argv[1])
    data = image_path.read_bytes()
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")

    body = {
        "model": "Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 768,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "price_tag_ocr", "schema": SCHEMA},
        },
    }

    r = requests.post("http://localhost:30000/v1/chat/completions", json=body, timeout=180)
    print("HTTP", r.status_code)
    r.raise_for_status()
    payload = r.json()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
