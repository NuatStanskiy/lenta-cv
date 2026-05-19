from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from .schemas import CropCandidate

FIELDNAMES = [
    "filename",
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
    "frame_timestamp",
    "x_min",
    "y_min",
    "x_max",
    "y_max",
    "qr_code_barcode",
    "price1_qr",
    "price2_qr",
    "price3_qr",
    "price4_qr",
    "wholesale_level_1_count",
    "wholesale_level_1_price",
    "wholesale_level_2_count",
    "wholesale_level_2_price",
    "action_price_qr",
    "action_code_qr",
]

LLM_FIELDS = [
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
QR_FIELDS = [
    "qr_code_barcode",
    "price1_qr",
    "price2_qr",
    "price3_qr",
    "price4_qr",
    "wholesale_level_1_count",
    "wholesale_level_1_price",
    "wholesale_level_2_count",
    "wholesale_level_2_price",
    "action_price_qr",
    "action_code_qr",
]


def build_rows(source_file: str, source_kind: str, items: list[tuple[CropCandidate, dict[str, Any]]]) -> list[dict[str, Any]]:
    filename_stem = Path(source_file).stem or source_file
    rows: list[dict[str, Any]] = []
    for crop, llm_payload in items:
        result = llm_payload.get("result", {}) if isinstance(llm_payload, dict) else {}
        if not isinstance(result, dict):
            result = {}
        llm_ok = bool(llm_payload.get("ok", False)) if isinstance(llm_payload, dict) else False

        x1, y1, x2, y2 = crop.bbox_xyxy
        frame_ts = int(round((crop.time_sec or 0.0) * 1000)) if source_kind == "video" else 0

        row: dict[str, Any] = {"filename": filename_stem}
        for k in LLM_FIELDS:
            row[k] = result.get(k, MISSING) if llm_ok else MISSING
        row["frame_timestamp"] = frame_ts
        row["x_min"] = x1
        row["y_min"] = y1
        row["x_max"] = x2
        row["y_max"] = y2
        for k in QR_FIELDS:
            row[k] = MISSING
        row["_crop_id"] = crop.crop_id
        row["_track_id"] = crop.track_id
        row["_frame_index"] = crop.frame_index
        row["_det_confidence"] = crop.confidence
        row["_llm_ok"] = llm_ok
        row["_llm_error"] = llm_payload.get("error") if isinstance(llm_payload, dict) else None
        rows.append(row)
    return rows


def rows_to_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buff = io.StringIO(newline="")
    writer = csv.DictWriter(buff, fieldnames=FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, MISSING) for k in FIELDNAMES})
    return buff.getvalue().encode("utf-8-sig")
