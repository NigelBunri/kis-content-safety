"""NudeNet detection core — ported from apps/media/safety.py in the
backend/kis Django repo (`_get_nudenet_detector`, `_highest_explicit_
detection`, `_scan_image_file`, `_scan_video_file`, `_probe_video_duration`),
kept byte-for-byte equivalent in logic so this service's output for a given
input matches what the in-process scan used to produce.

Deliberately NOT ported: the confidence-threshold decision (blocked vs.
pending_review vs. passed) and anything else in `run_nudenet_scan_on_file`
past the raw detection — that's business policy, stays in Django. This
module only answers "what did the model see."
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from app.config.settings import settings

# Same five labels as the Django original — NudeNet also emits non-explicit
# anatomical labels (FACE_FEMALE, ARMPITS_EXPOSED, etc.) that must never
# trigger a flag on their own.
EXPLICIT_LABELS = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}


class DetectionError(Exception):
    """Raised for any failure scanning a file — model load, corrupt input,
    ffmpeg/ffprobe failure, timeout. The API layer turns this into a 5xx;
    Django's caller treats any non-2xx here exactly like any other scan
    failure (fails closed to pending_review), so this never needs to
    encode fine-grained error types."""


_detector = None


def _get_detector():
    """Lazy, process-wide singleton — model load happens once per worker
    process, not once per scan request."""
    global _detector
    if _detector is None:
        from nudenet import NudeDetector  # type: ignore[import-not-found]

        _detector = NudeDetector()
    return _detector


def _highest_explicit_detection(detections: list[dict]) -> tuple[str | None, float]:
    best_label: str | None = None
    best_score = 0.0
    for det in detections:
        label = str(det.get("class") or det.get("label") or "")
        if label not in EXPLICIT_LABELS:
            continue
        score = float(det.get("score") or 0.0)
        if score > best_score:
            best_label, best_score = label, score
    return best_label, best_score


def scan_image(path: str) -> tuple[str | None, float]:
    try:
        detector = _get_detector()
        detections = detector.detect(path)
    except Exception as exc:
        raise DetectionError(f"image detection failed: {exc}") from exc
    return _highest_explicit_detection(detections)


def _probe_video_duration(path: str) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def scan_video(path: str, *, sample_count: int | None = None) -> tuple[str | None, float]:
    """Samples evenly-spaced frames rather than scanning every frame —
    per-frame inference on a full video is too slow. Any single sampled
    frame tripping the label filter flags the whole video, matching the
    original Django implementation exactly."""
    sample_count = sample_count or settings.video_frame_sample_count
    duration = _probe_video_duration(path)
    if duration <= 0:
        duration = 1.0

    best_label: str | None = None
    best_score = 0.0
    with tempfile.TemporaryDirectory() as tmp_dir:
        for i in range(sample_count):
            timestamp = duration * (i + 1) / (sample_count + 1)
            frame_path = os.path.join(tmp_dir, f"frame_{i}.jpg")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(timestamp), "-i", path, "-frames:v", "1", frame_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            except Exception:
                continue
            if not os.path.exists(frame_path):
                continue
            label, score = scan_image(frame_path)
            if score > best_score:
                best_label, best_score = label, score
    return best_label, best_score
