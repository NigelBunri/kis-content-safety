from app.config.settings import settings


def test_health_requires_no_auth(client):
    client.headers.pop("X-Internal-Auth")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_scan_requires_auth(client):
    client.headers.pop("X-Internal-Auth")
    response = client.post("/scan/image", files={"file": ("x.jpg", b"fake", "image/jpeg")})
    assert response.status_code == 401


def test_scan_rejects_wrong_token(client):
    client.headers.update({"X-Internal-Auth": "wrong"})
    response = client.post("/scan/image", files={"file": ("x.jpg", b"fake", "image/jpeg")})
    assert response.status_code == 401


def test_scan_fails_closed_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "")
    response = client.post("/scan/image", files={"file": ("x.jpg", b"fake", "image/jpeg")})
    assert response.status_code == 503
