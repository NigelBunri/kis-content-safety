import hmac

from fastapi import Header, HTTPException, status

from app.config.settings import settings


def require_internal_auth(x_internal_auth: str = Header(default="")) -> None:
    """Shared-secret auth, same shape as kisvideo's require_internal_auth —
    this service's only callers are Django's Celery workers, never end
    users directly."""
    if not settings.internal_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not configured with an internal token.",
        )
    if not x_internal_auth or not hmac.compare_digest(x_internal_auth, settings.internal_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing internal auth token.")
