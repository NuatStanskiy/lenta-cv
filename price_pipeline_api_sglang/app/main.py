from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))

from .config import Settings, get_settings
from .csv_writer import build_rows, rows_to_csv_bytes
from .detector import PriceTagDetector
from .image_ops import encode_jpeg
from .llm_client import LlmClient
from .media import media_kind, persist_upload, read_image_bgr
from .schemas import CropCandidate
from .security import check_api_key

settings = get_settings()
state: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["settings"] = settings
    state["detector"] = PriceTagDetector(settings)
    state["llm"] = LlmClient(settings)
    yield


app = FastAPI(title="Price tag pipeline API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    detector_ready = "detector" in state
    return {
        "ok": True,
        "service": settings.app_name,
        "detector_ready": detector_ready,
        "model_path": settings.model_path,
        "llm_api_url": settings.llm_api_url,
        "llm_endpoint": settings.llm_endpoint,
        "llm_backend": settings.llm_backend,
        "llm_model": settings.llm_model,
        "device": settings.device,
        "rotate": settings.rotate,
    }


@app.post("/process", dependencies=[Depends(check_api_key)])
async def process_media(file: UploadFile = File(...), skip_llm: bool = False) -> Response:
    """Accept image/video and return a CSV.

    Pipeline: upload -> rotate/fisheye -> price-tag-detection -> crop -> LLM API -> CSV.
    """
    detector = _detector()
    llm = _llm()

    path = await persist_upload(file)
    try:
        kind = media_kind(path, file.content_type)
        if kind == "image":
            candidates = detector.process_image(read_image_bgr(path))
        else:
            candidates = detector.process_video_file(path)

        if settings.save_debug_crops:
            _save_debug_crops(candidates)

        items = _run_llm_parallel(candidates, llm, skip_llm)

        rows = build_rows(file.filename or Path(path).name, kind, items)
        csv_bytes = rows_to_csv_bytes(rows)
        out_name = (Path(file.filename or "result").stem or "result") + ".csv"
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@app.post("/process_json", dependencies=[Depends(check_api_key)])
async def process_media_json(file: UploadFile = File(...), skip_llm: bool = False) -> JSONResponse:
    """Same pipeline as /process, but returns JSON rows for debugging/integration tests."""
    detector = _detector()
    llm = _llm()

    path = await persist_upload(file)
    try:
        kind = media_kind(path, file.content_type)
        candidates = detector.process_image(read_image_bgr(path)) if kind == "image" else detector.process_video_file(path)
        items = _run_llm_parallel(candidates, llm, skip_llm)
        rows = build_rows(file.filename or Path(path).name, kind, items)
        return JSONResponse({"ok": True, "count": len(rows), "rows": rows})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _detector() -> PriceTagDetector:
    detector = state.get("detector")
    if not isinstance(detector, PriceTagDetector):
        raise HTTPException(status_code=503, detail="Detector is not ready")
    return detector


def _llm() -> LlmClient:
    llm = state.get("llm")
    if not isinstance(llm, LlmClient):
        raise HTTPException(status_code=503, detail="LLM client is not ready")
    return llm


@app.post("/process_stream", dependencies=[Depends(check_api_key)])
async def process_stream(file: UploadFile = File(...), skip_llm: bool = False) -> StreamingResponse:
    """NDJSON-stream the pipeline so clients can show real-time progress.

    Events emitted (one per line, application/x-ndjson):
      {event:"start", kind, filename}
      {event:"detect_start", kind, frames_total, fps?, total_video_frames?}
      {event:"detect_frame", frame_index, frames_done, frames_total, tags_in_frame, tags_so_far, unique_tracks_so_far?}
      {event:"detected", count}                                # unique crops sent to LLM
      {event:"llm", crop_id, done, total, ok, error?}
      {event:"done", ok:true, count, rows:[...]}
      {event:"error", error}
    """
    detector = _detector()
    llm = _llm()

    path = await persist_upload(file)
    filename = file.filename or Path(path).name
    content_type = file.content_type

    async def gen():
        loop = asyncio.get_running_loop()
        events: queue.Queue = queue.Queue()

        def worker() -> None:
            try:
                kind = media_kind(path, content_type)
                events.put({"event": "start", "kind": kind, "filename": filename})

                if kind == "image":
                    candidates = detector.process_image(read_image_bgr(path), progress=events.put)
                else:
                    candidates = detector.process_video_file(path, progress=events.put)

                events.put({"event": "detected", "count": len(candidates)})

                total = len(candidates)
                items: list[tuple[CropCandidate, dict]] = []

                if skip_llm or total == 0:
                    items = [(c, {"ok": True, "result": {}}) for c in candidates]
                else:
                    workers = max(1, min(LLM_CONCURRENCY, total))

                    def call(crop: CropCandidate):
                        try:
                            return crop, llm.extract(crop)
                        except Exception as exc:
                            return crop, {"ok": False, "error": repr(exc), "result": {}}

                    done_count = 0
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = [pool.submit(call, c) for c in candidates]
                        for fut in as_completed(futures):
                            crop, payload = fut.result()
                            items.append((crop, payload))
                            done_count += 1
                            events.put({
                                "event": "llm",
                                "crop_id": crop.crop_id,
                                "done": done_count,
                                "total": total,
                                "ok": bool(payload.get("ok")),
                                "error": payload.get("error"),
                            })

                rows = build_rows(filename, kind, items)
                events.put({"event": "done", "ok": True, "count": len(rows), "rows": rows})
            except Exception as exc:
                events.put({"event": "error", "error": repr(exc)})
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                events.put(None)  # sentinel

        threading.Thread(target=worker, daemon=True).start()

        while True:
            ev = await loop.run_in_executor(None, events.get)
            if ev is None:
                break
            yield (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_llm_parallel(
    candidates: list[CropCandidate],
    llm: LlmClient,
    skip_llm: bool,
) -> list[tuple[CropCandidate, dict]]:
    if not candidates:
        return []
    if skip_llm:
        return [(c, {"ok": True, "result": {}}) for c in candidates]

    def call(crop: CropCandidate) -> dict:
        try:
            return llm.extract(crop)
        except Exception as exc:
            return {"ok": False, "error": repr(exc), "result": {}}

    workers = max(1, min(LLM_CONCURRENCY, len(candidates)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        payloads = list(pool.map(call, candidates))
    return list(zip(candidates, payloads))


def _save_debug_crops(candidates: list[CropCandidate]) -> None:
    out_dir = Path(settings.debug_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for cand in candidates:
        crop_path = out_dir / f"{cand.crop_id}.jpg"
        crop_path.write_bytes(encode_jpeg(cand.image_bgr))
