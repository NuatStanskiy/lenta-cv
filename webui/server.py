"""Minimal FastAPI proxy that serves the static UI and forwards uploads to the price-pipeline API.

ENV:
  PIPELINE_API_URL  - base URL of price-pipeline (default http://localhost:7860)
  PIPELINE_API_KEY  - optional X-API-Key if the pipeline is protected
  REQUEST_TIMEOUT   - seconds, default 1800 (videos can be slow)
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

PIPELINE_URL = os.getenv("PIPELINE_API_URL", "http://localhost:7860").rstrip("/")
PIPELINE_KEY = os.getenv("PIPELINE_API_KEY") or ""
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "1800"))

app = FastAPI(title="Price-tag pipeline UI")


def _headers() -> dict[str, str]:
    return {"X-API-Key": PIPELINE_KEY} if PIPELINE_KEY else {}


@app.get("/api/health")
async def health() -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{PIPELINE_URL}/health", headers=_headers())
        return JSONResponse(
            {"ok": True, "pipeline": r.json(), "pipeline_url": PIPELINE_URL},
            status_code=r.status_code,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": repr(exc), "pipeline_url": PIPELINE_URL},
            status_code=502,
        )


async def _forward(endpoint: str, file: UploadFile) -> httpx.Response:
    payload = await file.read()
    files = {"file": (file.filename or "upload.bin", payload, file.content_type or "application/octet-stream")}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        return await c.post(f"{PIPELINE_URL}{endpoint}", files=files, headers=_headers())


@app.post("/api/process")
async def process_json(file: UploadFile = File(...)) -> JSONResponse:
    try:
        r = await _forward("/process_json", file)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"pipeline unreachable: {exc!r}") from exc
    try:
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "non-JSON pipeline response", "raw": r.text[:2000]},
            status_code=502,
        )


@app.post("/api/process_csv")
async def process_csv(file: UploadFile = File(...)) -> Response:
    try:
        r = await _forward("/process", file)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"pipeline unreachable: {exc!r}") from exc
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "text/csv; charset=utf-8"),
        headers={"Content-Disposition": r.headers.get("content-disposition", 'attachment; filename="result.csv"')},
        status_code=r.status_code,
    )


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
