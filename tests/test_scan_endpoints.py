from unittest.mock import patch

from app.detector import DetectionError


def test_scan_image_returns_label_and_score(client):
    with patch("app.main.scan_image", return_value=("BUTTOCKS_EXPOSED", 0.91)):
        response = client.post("/scan/image", files={"file": ("x.jpg", b"fake-bytes", "image/jpeg")})
    assert response.status_code == 200
    assert response.json() == {"label": "BUTTOCKS_EXPOSED", "score": 0.91}


def test_scan_image_clean_result(client):
    with patch("app.main.scan_image", return_value=(None, 0.0)):
        response = client.post("/scan/image", files={"file": ("x.jpg", b"fake-bytes", "image/jpeg")})
    assert response.status_code == 200
    assert response.json() == {"label": None, "score": 0.0}


def test_scan_video_returns_label_and_score(client):
    with patch("app.main.scan_video", return_value=("FEMALE_BREAST_EXPOSED", 0.8)):
        response = client.post("/scan/video", files={"file": ("x.mp4", b"fake-bytes", "video/mp4")})
    assert response.status_code == 200
    assert response.json() == {"label": "FEMALE_BREAST_EXPOSED", "score": 0.8}


def test_scan_image_detection_error_returns_422(client):
    with patch("app.main.scan_image", side_effect=DetectionError("model load failed")):
        response = client.post("/scan/image", files={"file": ("x.jpg", b"fake-bytes", "image/jpeg")})
    assert response.status_code == 422


def test_scan_video_timeout_returns_504(client):
    import time

    from app.config.settings import settings

    def _slow_scan(path):
        time.sleep(0.2)
        return (None, 0.0)

    with patch("app.main.settings", settings), \
         patch.object(settings, "scan_timeout_seconds", 0.05), \
         patch("app.main.scan_video", side_effect=_slow_scan):
        response = client.post("/scan/video", files={"file": ("x.mp4", b"fake-bytes", "video/mp4")})
    assert response.status_code == 504
