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
