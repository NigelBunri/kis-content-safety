# KIS Content Safety — Deployment & Operations Guide

Mirrors the structure of the KIS AWS/Lightsail ops runbook (Django/Nest)
and kisvideo's own `docs/DEPLOYMENT.md` — same golden rules, same
build → tag → push → pull → recreate → health-check shape, adapted for
this service's single-container, no-database, no-queue design.

**Target:** the same Lightsail box as Django/Nest/chat/kisvideo
(`/opt/kis`) — not separate infra. Unlike kisvideo, this service does
**not** reuse `kis-postgres` or `kis-redis` — it doesn't need either.
It's stateless: every scan is a synchronous request/response with no job
table and no queue. See `docker-compose.prod.yml`'s header comment and
README.md's "Why a separate service, and why no queue/DB" section for the
full reasoning.

**Image:** `ghcr.io/nigelbunri/kis-content-safety` — one image, one
service (`api`), unlike kisvideo's api/worker/beat trio built from a
shared Dockerfile. There's no worker to separately tag here.

**Rollback tag format:** `YYYY-MM-DD-HHMM`, same convention as
kis-django/kis-nest/kisvideo.

**Before the first deploy — unresolved as of this writing:**
1. **Port 8020 is a proposal, not confirmed** against the real server's
   in-use ports the way kisvideo's 8010 was. Check `docker ps` /
   `ss -tlnp` on the box before deploying and update
   `docker-compose.prod.yml` if it collides.
2. **Memory fit — resolved 2026-09-07 with dev-3c.** The box had ~725MB
   free and was already 1.4GB into swap at kisvideo's own sizing check,
   before this service's footprint is added on top of kisvideo's 512M
   worker limit. Rather than accept the original draft's `max_workers=2`
   / 512M (worst case ~367MB), `app/main.py`'s `ThreadPoolExecutor` is
   now `max_workers=1` and the compose limit is 384M (worst case ~284MB,
   ~35% headroom) — see README.md's "Resource sizing" section for the
   full numbers. Revisit only if this box is resized or this service
   gets dedicated infra.
3. **Django-side settings — owned by dev-3c, not this repo.** `apps/media/
   content_safety_provider.py` (in `backend/kis`, currently on the
   `content-safety-integration` worktree/branch, not yet on `main`) reads
   `MEDIA_SAFETY_SERVICE_ENABLED`, `MEDIA_SAFETY_SERVICE_BASE_URL`, and
   `MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN` from Django settings — none of
   these exist in `backend/kis`'s real `.env` yet (checked). dev-3c is
   adding these ahead of this service going live, with
   `MEDIA_SAFETY_SERVICE_ENABLED` left off until the service is actually
   deployed and reachable — safe to land independently. For reference,
   once this service is deployed and reachable at its container name on
   `kis-production-network`, Django's `.env.production` needs:
   ```
   MEDIA_SAFETY_SERVICE_ENABLED=True
   MEDIA_SAFETY_SERVICE_BASE_URL=http://kis-content-safety-api-1:8000
   MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN=<same value as this service's CONTENT_SAFETY_INTERNAL_TOKEN>
   ```
   (container name depends on the actual compose project name Docker
   assigns — confirm with `docker ps` after first deploy rather than
   assuming the name above is exact.) Coordinate this edit with whoever
   owns `backend/kis`'s `.env.production` — it's a shared, real
   production file, not something to hand-edit from a guess.

---

## Golden safety rules

Identical to the Django/Nest/kisvideo runbooks — repeated here, not
assumed:

- Always run `git status` before pulling, building, committing, or
  pushing.
- Never commit or paste `.env` files, internal tokens, or any other
  credential.
- Back up `.env.production` before editing it
  (`cp .env.production .env.production.before-edit.$(date +%F-%H%M%S)`).
- Run the health check after every restart.
- This service owns no shared containers (no `kis-postgres`/`kis-redis`
  dependency at all) — a `docker compose down` here has no `-v` volume
  risk the way kisvideo's does, since this file defines no named volumes.
  Still never run `docker compose down` on the shared
  `kis-production-network` definition itself, or anything that could
  affect other services attached to it.

---

## Daily health check

```
cd /opt/kis-content-safety
docker compose -f docker-compose.prod.yml ps
docker stats --no-stream api   # this box is tight on RAM - worth a daily glance
curl -sS -H "X-Internal-Auth: $CONTENT_SAFETY_INTERNAL_TOKEN" http://localhost:8020/health
```

Healthy baseline: `api` running (`Up`), no restart-looping. `/health`
doesn't require auth and doesn't exercise the model — it only confirms
the process is alive, not that NudeNet loaded successfully. A more
thorough check (worth doing after any restart, not just routinely) is a
real scan call — see "Validate" below.

---

## Full deployment

Use only after code is committed and pushed.

```
cd /opt/kis-content-safety
git status
git log --oneline -3
git pull origin main
git log --oneline -3
```

### Build and push

```
cd /opt/kis-content-safety
docker build -t kis-content-safety:production .

DATE_TAG=$(date +%F-%H%M)
docker tag kis-content-safety:production ghcr.io/nigelbunri/kis-content-safety:production
docker tag kis-content-safety:production ghcr.io/nigelbunri/kis-content-safety:$DATE_TAG
docker push ghcr.io/nigelbunri/kis-content-safety:production
docker push ghcr.io/nigelbunri/kis-content-safety:$DATE_TAG
echo "Rollback tag: $DATE_TAG"
```

Model weights are baked into the image at build time (see README.md's
"Model weights" section) — expect this build to take noticeably longer
than a typical Python service image while that bake-in step runs, this
is expected, not a hang.

### Deploy

The warmup call between `up -d` and traffic is not optional — see the
caveat below the block for why. Do this every deploy, not just the first
one.

```
cd /opt/kis-content-safety
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --force-recreate

# Warmup: force model load now, on our own terms, instead of letting the
# first real request (possibly from Django, with a 30s client timeout)
# absorb a cold-start cost that measured ~49s locally. See the caveat
# below for what this number is and isn't confirmed to mean.
docker compose -f docker-compose.prod.yml exec api python -c "from app.detector import _get_detector; _get_detector()"
```

If deploying alongside enabling `MEDIA_SAFETY_SERVICE_ENABLED` on the
Django side for the first time, run the warmup call above and confirm it
returns before flipping that flag on — don't let Django's first real
scan request be the thing that discovers a cold-start problem.

### Validate

```
cd /opt/kis-content-safety
docker compose -f docker-compose.prod.yml ps
curl -i http://localhost:8020/health
docker compose -f docker-compose.prod.yml logs api --tail=100
```

Then a real scan call, not just `/health` — confirms the model actually
loaded and inference works end to end, not just that the process started
(and, having already run the warmup above, this should now be fast):

```
curl -sS -X POST http://localhost:8020/scan/image \
  -H "X-Internal-Auth: $CONTENT_SAFETY_INTERNAL_TOKEN" \
  -F "file=@/path/to/any/small/test/image.jpg"
```

Expect `{"label": null or a string, "score": <float>}`, not a 5xx.

**Why the warmup call exists:** measured locally, a truly first-ever model
load in a fresh environment took ~49s before settling to sub-second on
every call after (see README.md's "Resource sizing" section for the full
measurement and the caveat that the exact cause wasn't fully isolated —
evidence points at cold OS-level file-cache for the bundled ONNX weights /
onnxruntime's shared library, not a network download, since the weights
ship inside the `nudenet` pip package and are baked into the image).
Django's client (`content_safety_provider.py`) uses a 30s timeout for
image scans, so an unwarmed first request could time out client-side even
though the service would have succeeded a few seconds later. The warmup
step in "Deploy" above closes this gap by paying that cost deliberately,
before Django ever sends real traffic.

---

## Rollback

```
cd /opt/kis-content-safety
ROLLBACK_TAG="YYYY-MM-DD-HHMM"
docker pull ghcr.io/nigelbunri/kis-content-safety:$ROLLBACK_TAG
docker tag ghcr.io/nigelbunri/kis-content-safety:$ROLLBACK_TAG ghcr.io/nigelbunri/kis-content-safety:production
docker compose -f docker-compose.prod.yml up -d --force-recreate
docker compose -f docker-compose.prod.yml ps
curl -i http://localhost:8020/health
```

No database/schema to worry about on rollback — this service has none.

---

## Weekly

```
cd /opt/kis-content-safety
git status
git log --oneline -5
docker compose -f docker-compose.prod.yml logs api --since=168h 2>&1 | grep -iE 'error|exception|traceback' | tail -100
```

Watch specifically for `504` (scan timeout, `CONTENT_SAFETY_SCAN_TIMEOUT_
SECONDS`, default 45s) and `422` (`DetectionError`) rates — a spike in
either points at a real problem (corrupt uploads reaching this service,
or the model/ffmpeg genuinely struggling under load), not routine noise.

---

## Monthly cleanup

No database, no named volumes, no upload staging directory to sweep —
this service leaves nothing behind between requests. Only routine image/
build-cache cleanup applies, same as every other service on this box:

```
docker builder prune --filter "until=168h"
docker image prune -a --filter "until=168h"
```

---

## Troubleshooting

**api returns 500/502:**
1. `docker compose -f docker-compose.prod.yml ps` — healthy or
   restarting? A restart loop here is very likely an OOM kill — see
   "Container OOM-killed" below before assuming it's a code bug.
2. `docker compose -f docker-compose.prod.yml logs api --tail=200`

**Every scan returns 401:**
`X-Internal-Auth` header missing or not matching
`CONTENT_SAFETY_INTERNAL_TOKEN` in `.env.production` — confirm it's the
exact same value configured on the Django side
(`MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN`). A mismatch here fails closed
(Django's `content_safety_provider.py` treats any non-2xx as a scan
failure, which the caller then handles as `pending_review`, per
`apps/media/safety.py`) — silent-looking on the Django side, so check
this service's own logs/response, not just Django's.

**Every scan returns 504:**
Scan exceeded `CONTENT_SAFETY_SCAN_TIMEOUT_SECONDS` (default 45s). Check
whether this is a genuinely large/long video (video scans do 5 sampled
ffmpeg frame extractions + 5 inferences, sequentially) or the container
is CPU-starved by other traffic on the shared box (`docker stats`) — a
sustained spike here on normal-sized files points at CPU contention, not
this service's own logic.

**Container OOM-killed / restarting under load:**
See README.md's "Resource sizing" section — the 384M limit here is a
reasoned worst-case estimate (measured Python-process peak + a reused,
not independently measured, ffmpeg-subprocess figure), not as tightly
verified as kisvideo's own 512M figure. `app/main.py`'s
`ThreadPoolExecutor` is already `max_workers=1` (dropped from an earlier
2, specifically for this box's tight memory — see README.md), so an OOM
here means either that assumption undercounted something (e.g. a much
larger/longer video than tested, or genuine memory pressure from other
services on the box at the same moment — check `docker stats` across all
containers, not just this one) rather than a concurrency fix to apply. If
it recurs, the real options are raising the limit (only if the box
actually has room — it may not, see kisvideo's own header comment) or
moving this service off the shared box entirely.

**SSH disconnects mid-command:** same as every other service on this
box — "Connection reset by peer" usually just means the SSH session
ended, not that the service stopped. Reconnect and re-run the daily
health check.
