from __future__ import annotations

import json
import re
from typing import Any

EXPECTED_KEYS = [
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

MISSING = "Нет"


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"JSON object not found in model output: {text[:500]}")
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("Model output JSON is not an object")
    return obj


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in {"", "нет", "none", "null", "n/a", "na", "-"}


def normalize_price(value: Any) -> str:
    if _is_missing(value):
        return MISSING
    s = str(value).strip().replace(" ", "").replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    m = re.search(r"(\d{1,6})\.(\d{2})", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 4:
        return f"{digits[:-2]}.{digits[-2:]}"
    return s or MISSING


def normalize_discount(value: Any) -> str:
    if _is_missing(value):
        return MISSING
    s = str(value)
    m = re.search(r"-?\s*(\d{1,2})\s*%", s)
    return f"-{m.group(1)}%" if m else str(value)


def normalize_barcode(value: Any) -> str:
    if _is_missing(value):
        return MISSING
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) >= 6 else MISSING


def postprocess(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in EXPECTED_KEYS:
        val = data.get(key)
        out[key] = MISSING if _is_missing(val) else str(val).strip()

    out["price_default"] = normalize_price(out["price_default"])
    out["price_card"] = normalize_price(out["price_card"])
    out["price_discount"] = normalize_price(out["price_discount"])
    out["discount_amount"] = normalize_discount(out["discount_amount"])
    out["barcode"] = normalize_barcode(out["barcode"])
    out["color"] = normalize_color(out["color"])
    return out


def normalize_color(value: Any) -> str:
    if _is_missing(value):
        return MISSING
    s = str(value).strip().lower()
    if "red" in s or "красн" in s:
        return "red"
    if "yellow" in s or "жёлт" in s or "желт" in s:
        return "yellow"
    return MISSING
