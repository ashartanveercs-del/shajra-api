from io import BytesIO
from types import SimpleNamespace

import main
from coordination import CoordinationError
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request


client = TestClient(main.app)


def _enable_public_writes():
    main.app.dependency_overrides[main.require_public_writes] = lambda: None
    main.app.dependency_overrides[main.require_upload_rate_limit] = lambda: None


def _png_bytes():
    content = BytesIO()
    Image.new("RGB", (2, 2), color="white").save(content, format="PNG")
    return content.getvalue()


def _animated_gif_bytes():
    content = BytesIO()
    frames = [
        Image.new("RGB", (2, 2), color="white"),
        Image.new("RGB", (2, 2), color="black"),
    ]
    frames[0].save(
        content,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return content.getvalue()


def test_upload_rejects_invalid_media_type_without_calling_cloudinary(monkeypatch):
    _enable_public_writes()
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid media reached Cloudinary")
        ),
    )
    try:
        response = client.post(
            "/api/upload-image",
            files={"file": ("notes.txt", b"not an image", "text/plain")},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid file type. Only standard images are allowed."


def test_upload_rejects_oversized_image_without_calling_cloudinary(monkeypatch):
    _enable_public_writes()
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("oversized media reached Cloudinary")
        ),
    )
    try:
        response = client.post(
            "/api/upload-image",
            files={
                "file": (
                    "large.png",
                    b"x" * (main.MAX_IMAGE_UPLOAD_BYTES + 1),
                    "image/png",
                )
            },
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 413
    assert response.json()["detail"] == "Image exceeds the 10 MB upload limit."


def test_upload_rejects_oversized_http_body_before_route_dependencies():
    dependency_called = False

    def public_writes():
        nonlocal dependency_called
        dependency_called = True

    main.app.dependency_overrides[main.require_public_writes] = public_writes
    main.app.dependency_overrides[main.require_upload_rate_limit] = lambda: None
    try:
        response = client.post(
            "/api/upload-image",
            content=b"",
            headers={
                "content-type": "multipart/form-data; boundary=oversized",
                "content-length": str(main.MAX_IMAGE_UPLOAD_BYTES + 65_537),
            },
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 413
    assert dependency_called is False


def test_upload_rejects_oversized_stream_without_content_length(monkeypatch):
    _enable_public_writes()
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("oversized stream reached Cloudinary")
        ),
    )
    chunks = (b"x" * 1024 * 1024 for _ in range(11))
    try:
        response = client.post(
            "/api/upload-image",
            content=chunks,
            headers={
                "content-type": "multipart/form-data; boundary=oversized",
                "transfer-encoding": "chunked",
            },
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 413
    assert response.json()["detail"] == "Image exceeds the 10 MB upload limit."


def test_request_ip_ignores_forwarded_header_outside_vercel(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/upload-image",
            "headers": [(b"x-forwarded-for", b"203.0.113.7")],
            "client": ("127.0.0.1", 12345),
        }
    )

    assert main._request_ip(request) == "127.0.0.1"


def test_request_ip_uses_vercel_forwarded_header_on_vercel(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/upload-image",
            "headers": [(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")],
            "client": ("127.0.0.1", 12345),
        }
    )

    assert main._request_ip(request) == "203.0.113.7"


def test_upload_returns_cloudinary_secure_url(monkeypatch):
    _enable_public_writes()
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda content, **_kwargs: {
            "secure_url": "https://images.example.invalid/profile.png",
            "bytes": len(content),
        },
    )
    try:
        response = client.post(
            "/api/upload-image",
            files={"file": ("profile.png", _png_bytes(), "image/png")},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "url": "https://images.example.invalid/profile.png"
    }


def test_upload_rejects_spoofed_image_content(monkeypatch):
    _enable_public_writes()
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("spoofed media reached Cloudinary")
        ),
    )
    try:
        response = client.post(
            "/api/upload-image",
            files={"file": ("spoofed.png", b"not a png", "image/png")},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is not a valid image."


def test_upload_rejects_animated_image_over_aggregate_pixel_budget(monkeypatch):
    _enable_public_writes()
    monkeypatch.setattr(main, "MAX_IMAGE_TOTAL_PIXELS", 4, raising=False)
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("over-budget animation reached Cloudinary")
        ),
    )
    try:
        response = client.post(
            "/api/upload-image",
            files={"file": ("animated.gif", _animated_gif_bytes(), "image/gif")},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is not a valid image."


def test_upload_rate_limit_blocks_before_cloudinary(monkeypatch):
    class DenyingRateLimiter:
        def consume(self, policy, subject, request_nonce):
            return SimpleNamespace(allowed=False, retry_after_ms=61_000)

    main.app.dependency_overrides[main.require_public_writes] = lambda: None
    main.app.dependency_overrides[main.get_upload_rate_limiter] = DenyingRateLimiter
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rate-limited media reached Cloudinary")
        ),
    )
    try:
        response = client.post(
            "/api/upload-image",
            headers={"x-forwarded-for": "203.0.113.7"},
            files={"file": ("profile.png", _png_bytes(), "image/png")},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "61"
    assert response.json()["detail"]["code"] == "RATE_LIMITED"


def test_upload_fails_closed_when_distributed_rate_limiter_is_unavailable(monkeypatch):
    main.app.dependency_overrides[main.require_public_writes] = lambda: None
    monkeypatch.setattr(
        main.runtime_coordination,
        "build_rate_limiter",
        lambda _settings: (_ for _ in ()).throw(
            CoordinationError("COORDINATION_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        main.cloudinary.uploader,
        "upload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unprotected media reached Cloudinary")
        ),
    )
    try:
        response = client.post(
            "/api/upload-image",
            files={"file": ("profile.png", _png_bytes(), "image/png")},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "COORDINATION_UNAVAILABLE",
        "message": "Upload protection is temporarily unavailable.",
    }
