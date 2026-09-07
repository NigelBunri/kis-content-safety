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
2. **Memory fit is a real open question, not just a limit to set.** See
   README.md's "Resource sizing" section — the box had ~725MB free and
   was already 1.4GB into swap at kisvideo's last check, before this
   service's own ~367MB worst-case footprint is added on top of
   kisvideo's 512M worker limit. This was flagged back to the team
   (dev-3c) rather than resolved unilaterally in this pass — confirm the
   actual resolution (reduced concurrency, a bigger box, scans staggered
   away from active transcodes, etc.) before treating the 512M limit in
   `docker-compose.prod.yml` as safe to deploy as-is.
3. **Django-side settings are not yet wired.** `apps/media/
   content_safety_provider.py` (in `backend/kis`, currently on the
   `content-safety-integration` worktree/branch, not yet on `main`) reads
   `MEDIA_SAFETY_SERVICE_ENABLED`, `MEDIA_SAFETY_SERVICE_BASE_URL`, and
   `MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN` from Django settings — none of
   these exist in `backend/kis`'s real `.env` yet (checked). Once this
   service is deployed and reachable at its container name on
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

```
cd /opt/kis-content-safety
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

### Validate

```
cd /opt/kis-content-safety
docker compose -f docker-compose.prod.yml ps
curl -i http://localhost:8020/health
docker compose -f docker-compose.prod.yml logs api --tail=100
```

Then a real scan call, not just `/health` — confirms the model actually
loaded and inference works end to end, not just that the process started:

```
curl -sS -X POST http://localhost:8020/scan/image \
  -H "X-Internal-Auth: $CONTENT_SAFETY_INTERNAL_TOKEN" \
  -F "file=@/path/to/any/small/test/image.jpg"
```

Expect `{"label": null or a string, "score": <float>}`, not a 5xx.

**A cold first request can be slow.** Measured locally: a truly first-ever
model load in a fresh environment took ~49s before settling to
sub-second on every call after (see README.md's "Resource sizing"
section for the full measurement and the caveat that the exact cause
wasn't fully isolated — evidence points at cold OS-level file-cache for
the bundled ONNX weights / onnxruntime's shared library, not a network
download, since the weights ship inside the `nudenet` pip package and are
baked into the image). Django's client (`content_safety_provider.py`)
uses a 30s timeout for image scans — if the very first production
request after a fresh container start hits this, it could time out
client-side even though the service would have succeeded given a few
more seconds. Worth a deliberate warmup call (e.g. `docker compose ...
exec api python -c "from app.detector import _get_detector;
_get_detector()"` right after `up -d`, before traffic is expected) rather
than trusting the first real user request to absorb this cost — not yet
automated into the deploy steps above, flagging as a real gap rather than
quietly ignoring it.

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
See README.md's "Resource sizing" section — the 512M limit here is a
reasoned worst-case estimate (measured Python-process peak + a reused,
not independently measured, ffmpeg-subprocess figure), not as tightly
verified as kisvideo's own 512M figure. If this happens in practice:
1. Check whether 2 video scans landed concurrently
   (`app/main.py`'s `ThreadPoolExecutor(max_workers=2)`) — the estimate's
   worst case assumes exactly this.
2. Consider reducing `max_workers` to 1 in `app/main.py` (a code change,
   not a config flag today) if this box can't sustain 2 concurrent scans
   alongside kisvideo/Django/Nest — mirrors kisvideo's own
   `--concurrency=1` reduction for the same CPU/RAM-constrained-box
   reason.

**SSH disconnects mid-command:** same as every other service on this
box — "Connection reset by peer" usually just means the SSH session
ended, not that the service stopped. Reconnect and re-run the daily
health check.
