from __future__ import annotations

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import require_internal_auth
from app.config.settings import settings
from app.detector import DetectionError, scan_image, scan_video

app = FastAPI(title="KIS Content Safety")

# A blocking-call executor separate from FastAPI's default one, sized small
# on purpose — NudeNet inference is CPU-bound and this service is meant to
# run a handful of scans at a time, not a request-per-thread web server
# workload. Bounding concurrency here also bounds memory (each in-flight
# scan holds a full video's sampled frames / the model's own working set).
#
# max_workers=1, not 2: this runs on the same shared Lightsail box as
# Django/Nest/chat/kisvideo, which was already at ~725MB free and 1.4GB
# into swap before this service's own footprint was added (see kisvideo's
# docker-compose.prod.yml header and this repo's README.md "Resource
# sizing" section for the measured numbers). 2 concurrent video scans
# would mean 2 concurrent ffmpeg subprocesses on top of the shared model's
# own resident memory — 1 keeps the worst case to a single ffmpeg
# subprocess, halving that peak. Revisit if this box is ever resized or
# this service gets dedicated infra.
_executor = ThreadPoolExecutor(max_workers=1)


class ScanResponse(BaseModel):
    label: str | None
    score: float


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "kis-content-safety"}


async def _run_with_timeout(func, *args):
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, func, *args),
            timeout=settings.scan_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Scan exceeded {settings.scan_timeout_seconds}s timeout.",
        ) from exc


async def _save_upload_to_tempfile(upload: UploadFile, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
    except Exception:
        os.remove(path)
        raise
    return path


@app.post("/scan/image", response_model=ScanResponse, dependencies=[Depends(require_internal_auth)])
async def scan_image_endpoint(file: UploadFile) -> ScanResponse:
    path = await _save_upload_to_tempfile(file, suffix=".img")
    try:
        label, score = await _run_with_timeout(scan_image, path)
    except DetectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.remove(path)
    return ScanResponse(label=label, score=score)


@app.post("/scan/video", response_model=ScanResponse, dependencies=[Depends(require_internal_auth)])
async def scan_video_endpoint(file: UploadFile) -> ScanResponse:
    path = await _save_upload_to_tempfile(file, suffix=".vid")
    try:
        label, score = await _run_with_timeout(scan_video, path)
    except DetectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.remove(path)
    return ScanResponse(label=label, score=score)
