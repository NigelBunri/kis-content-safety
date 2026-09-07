# kis-content-safety

Self-hosted explicit-content detection service, wrapping [NudeNet](https://github.com/notAI-tech/NudeNet)
behind a small internal HTTP API. Extracted out of the `backend/kis` Django
monolith (`apps/media/safety.py`) so the (large, CPU-bound) model inference
runs in its own container instead of inside the Django app server process.

## Scope

This service is a **dumb detector wrapper**, not a policy engine. It answers
"what did NudeNet detect in this file, filtered to the labels this platform
considers disqualifying" — it does **not** decide pass/block/review. That
decision (confidence threshold, status/quarantine mapping, user-facing
messages) stays in Django (`apps/media/safety.py::run_nudenet_scan_on_file`),
unchanged in shape from before this service existed — only the actual
NudeNet inference call moves out of-process. This keeps the policy threshold
tunable in Django without a service redeploy, and keeps this service free of
any business-logic drift risk.

Endpoints:
- `GET /health`
- `POST /scan/image` — multipart file upload, image bytes. Returns
  `{"label": str | null, "score": float}` — the highest-confidence detection
  among the explicit-label set, or `label: null` if none found.
- `POST /scan/video` — multipart file upload, video bytes. Samples 5 evenly
  spaced frames via `ffmpeg` and returns the same shape as `/scan/image`,
  taking the highest-scoring sampled frame.

Both routes require `X-Internal-Auth: <INTERNAL_TOKEN>` — same shared-secret
pattern as kisvideo's `require_internal_auth`, no per-request HMAC signing
(matches what's already scaffolded, a single shared token, not a signing
key pair).

## Why a separate service, and why no queue/DB

Unlike kisvideo (which needs tus resumable uploads, a Postgres job table, and
Celery/Redis for a multi-minute transcode pipeline), this service is entirely
stateless: no persistence, no job queue. Every call is synchronous
request/response — the one caller that matters (Django's async
`push_content_safety_scan` Celery task, see the `backend/kis` repo) is
already running off the request path, so it can simply block on an HTTP call
with a generous timeout rather than needing this service to hand back a job
id and call a webhook later the way kisvideo does.

## Model weights

NudeNet downloads its ONNX weights on first `NudeDetector()` instantiation if
they aren't already present in its cache directory. This repo's `Dockerfile`
bakes the weights into the image at build time (a throwaway container run
during the build primes the cache, then the resulting layer is kept) — the
monolith never did this (first real scan always ate a cold-start download),
and this service deliberately doesn't carry that gap forward.

## Resource sizing

Measured locally (`/usr/bin/time -l` around the actual process — same
discipline as kisvideo's own resource-sizing pass, not a guess), on macOS
arm64, not the real Lightsail deployment target:

- **Python process peak RSS** across model load + two image scans + one
  video scan, all in one process lifetime: **~184–201MB** (two separate
  runs, both in that range). This is the model (ONNX weights + onnxruntime
  session) plus FastAPI/uvicorn/numpy overhead — the dominant, steady-state
  cost.
- **ffmpeg frame-extraction subprocess** (`scan_video`'s `-frames:v 1` calls):
  not independently measured in this repo — reused from kisvideo's directly
  comparable thumbnail-extraction measurement (same operation shape:
  single-frame grab, no encoding), which measured **~83MB** peak RSS for
  that subprocess. Treat this figure as a reasonable stand-in, not a
  verified number for this exact codebase.
- **Concurrency**: `app/main.py`'s `ThreadPoolExecutor` is deliberately
  `max_workers=1`, not 2 — see below. The model itself is a process-wide
  singleton shared across threads (doesn't duplicate per concurrent scan);
  with `max_workers=1` there's never more than one ffmpeg subprocess
  running at a time either.
- **Worst-case estimate at max_workers=1**: ~201MB (process baseline) +
  ~83MB (one ffmpeg subprocess) ≈ **~284MB**. `docker-compose.prod.yml`
  sets a 384M limit — ~35% headroom over that estimate.

**Real capacity concern, resolved 2026-09-07 with dev-3c:** the box this
deploys to (same one as Django/Nest/chat/kisvideo) had ~725MB free and was
already 1.4GB into swap at kisvideo's own sizing check, before this
service's footprint is added on top of kisvideo's own 512M worker limit.
The original draft assumed `max_workers=2` (worst case ~367MB, a 512M
limit) — too much margin eaten on an already-tight box. Deliberately
dropped to `max_workers=1` (worst case ~284MB, a 384M limit) instead of
resizing the box, as the chosen tradeoff for this shared-infra deployment.
Revisit if this box is ever resized or this service gets dedicated infra.

**Cold-start latency, separately from memory:** a truly first-ever model
load in a fresh local environment took **~49 seconds** (import + first
`NudeDetector()` instantiation + first inference, not isolated from each
other in that run) before a second, separate run — same code, same
machine, weights already resident from the first run — completed the
equivalent work in under a second (0.48s model load, 0.05–0.07s per image
scan). The gap is real and reproducible, but its exact cause wasn't fully
isolated (most likely a cold OS-level file-cache / dynamic-linker cost for
the bundled ONNX weights and onnxruntime's shared library on first touch,
not a network fetch — the weights ship inside the `nudenet` pip package
itself and are baked into the image, so there's no download to blame).
Practical implication: don't assume the first request after a fresh
container start responds quickly — see `docs/DEPLOYMENT.md`'s warmup-call
recommendation and the timeout-mismatch risk against Django's 30s image
client timeout.
