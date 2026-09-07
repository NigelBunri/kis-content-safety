from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONTENT_SAFETY_")

    internal_token: str = ""
    # Wall-clock budget for a single scan call (frame extraction + inference
    # combined for video; just inference for images) — bounds a hung/slow
    # NudeDetector.detect() call or a stuck ffmpeg subprocess, neither of
    # which had any timeout in the original in-process Django code. A scan
    # that exceeds this is reported as a scan error (fail-closed on the
    # Django side, same as any other scan exception), not left hanging.
    scan_timeout_seconds: float = 45.0
    video_frame_sample_count: int = 5


settings = Settings()
