"""Minimal FastAPI proxy that serves the static UI and forwards uploads to the price-pipeline API.

ENV:
  PIPELINE_API_URL  - base URL of price-pipeline (default http://localhost:7860)
  PIPELINE_API_KEY  - optional X-API-Key if the pipeline is protected
  REQUEST_TIMEOUT   - seconds, default 1800 (videos can be slow)
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

PIPELINE_URL = os.getenv("PIPELINE_API_URL", "http://localhost:7860").rstrip("/")
PIPELINE_KEY = os.getenv("PIPELINE_API_KEY") or ""
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "1800"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "3"))
# Catch every httpx error: SSH tunnels die mid-stream all the time; we want the browser
# to see a clean NDJSON {"event":"error"} line instead of a 502.
TRANSIENT_ERRORS = (httpx.HTTPError,)

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
    fname = file.filename or "upload.bin"
    ctype = file.content_type or "application/octet-stream"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for attempt in (0, 1):
            try:
                return await c.post(
                    f"{PIPELINE_URL}{endpoint}",
                    files={"file": (fname, payload, ctype)},
                    headers=_headers(),
                )
            except TRANSIENT_ERRORS:
                if attempt == 1:
                    raise
                await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError("unreachable")  # for type-checkers


@app.post("/api/process")
async def process_stream(file: UploadFile = File(...)) -> StreamingResponse:
    """Stream the pipeline NDJSON to the browser so UI can show per-event progress."""
    payload = await file.read()
    fname = file.filename or "upload.bin"
    ctype = file.content_type or "application/octet-stream"

    async def gen():
        client = httpx.AsyncClient(timeout=TIMEOUT)
        # if the tunnel dies mid-stream we must still emit a well-formed NDJSON line so the
        # frontend sees error: ... instead of a generic 'Failed to fetch'.
        emitted_done = False
        try:
            async with client.stream(
                "POST",
                f"{PIPELINE_URL}/process_stream",
                files={"file": (fname, payload, ctype)},
                headers=_headers(),
            ) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    yield (json.dumps({
                        "event": "error",
                        "error": f"pipeline returned {r.status_code}",
                        "raw": body.decode("utf-8", "replace")[:2000],
                    }) + "\n").encode("utf-8")
                    return
                buffer = b""
                async for chunk in r.aiter_raw():
                    buffer += chunk
                    # advance to last full line to know if we already passed "done"
                    while b"\n" in buffer:
                        line, _, buffer = buffer.partition(b"\n")
                        if b'"event": "done"' in line or b'"event":"done"' in line:
                            emitted_done = True
                        yield line + b"\n"
                if buffer:
                    yield buffer
        except (TRANSIENT_ERRORS) as exc:
            if not emitted_done:
                yield (json.dumps({
                    "event": "error",
                    "error": f"pipeline connection dropped: {type(exc).__name__}: {exc}",
                }) + "\n").encode("utf-8")
        except Exception as exc:  # last-resort guard so we never return 502
            if not emitted_done:
                yield (json.dumps({
                    "event": "error",
                    "error": f"proxy error: {type(exc).__name__}: {exc}",
                }) + "\n").encode("utf-8")
        finally:
            await client.aclose()

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/process_json")
async def process_json(file: UploadFile = File(...)) -> JSONResponse:
    """Synchronous fallback for clients that don't speak NDJSON streaming."""
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
