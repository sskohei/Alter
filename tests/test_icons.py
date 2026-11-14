import io
import json
from uuid import uuid4

import httpx
import pytest
from alter_domain.icons import (
    ICON_BUCKET,
    MAX_BYTES,
    IconStorage,
    IconStorageUnavailable,
    InvalidIcon,
    normalize_image,
    validate_path,
)
from alter_domain.settings import Settings
from PIL import Image, PngImagePlugin
from pydantic import SecretStr


def image_bytes(format="PNG", size=(4, 4)):
    out = io.BytesIO()
    image = Image.new("RGB", size, "red")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "GPS-and-private-note")
    kwargs = {"pnginfo": metadata} if format == "PNG" else {}
    image.save(out, format=format, **kwargs)
    return out.getvalue()


@pytest.mark.parametrize(
    "format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_decode_and_reencode_removes_metadata_and_trailing_bytes(format, mime):
    data = normalize_image(image_bytes(format) + b"<script>trailing</script>", mime)
    assert b"<script>" not in data and b"GPS-and-private-note" not in data
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert not image.info
        image.verify()


@pytest.mark.parametrize(
    "body,mime",
    [
        (b"", "image/png"),
        (b"<svg/>", "image/svg+xml"),
        (b"<svg/>", "image/png"),
        (image_bytes("JPEG"), "image/png"),
        (image_bytes()[:30], "image/png"),
        (b"x" * (MAX_BYTES + 1), "image/png"),
        (image_bytes(size=(2049, 1)), "image/png"),
    ],
)
def test_invalid_size_mime_dimensions_or_corrupt_image(body, mime):
    with pytest.raises(InvalidIcon):
        normalize_image(body, mime)


def test_animated_image_is_rejected():
    out = io.BytesIO()
    first, second = Image.new("RGB", (4, 4), "red"), Image.new("RGB", (4, 4), "blue")
    first.save(out, format="WEBP", save_all=True, append_images=[second], duration=10, loop=0)
    with pytest.raises(InvalidIcon):
        normalize_image(out.getvalue(), "image/webp")


def test_foreign_and_noncanonical_paths_rejected():
    owner = uuid4()
    validate_path(f"{owner}/{uuid4()}.png", owner)
    for path in [
        f"{uuid4()}/{uuid4()}.png",
        f"{owner}/../secret",
        "https://evil.test/a.png",
        f"profile-private/{owner}/{uuid4()}.png",
        f"{owner}/{'-' * 36}.png",
    ]:
        with pytest.raises(InvalidIcon):
            validate_path(path, owner)


def storage(handler, secret="test-storage-key"):
    return IconStorage(
        Settings(_env_file=None, supabase_storage_secret_key=SecretStr(secret)),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_upload_is_non_overwriting_and_signing_uses_fixed_bucket_and_ttl():
    seen = []

    def handler(request):
        seen.append(request)
        if "/object/sign/" in request.url.path:
            assert json.loads(request.content) == {"expiresIn": 300}
            return httpx.Response(
                200,
                json={
                    "signedURL": request.url.path.removeprefix("/storage/v1") + "?token=test-only"
                },
            )
        assert request.headers["x-upsert"] == "false"
        assert request.headers["content-type"] == "image/png"
        return httpx.Response(201, json={"Key": "unused"})

    client = storage(handler)
    owner = uuid4()
    path = client.upload(owner, image_bytes())
    validate_path(path, owner)
    url = client.sign(path)
    assert url.startswith(f"http://127.0.0.1:54321/storage/v1/object/sign/{ICON_BUCKET}/{path}?")
    assert len(seen) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"signedURL": "https://evil.test/image"},
        {"signedURL": "/object/sign/profile-private/secret?token=x"},
        {"signedURL": "/object/sign/agent-icons/other?token=x"},
        {"signedURL": None},
    ],
)
def test_signing_cannot_return_arbitrary_url_or_private_bucket(payload):
    client = storage(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(IconStorageUnavailable):
        client.sign(f"{uuid4()}/{uuid4()}.png")


@pytest.mark.parametrize("status", [302, 401, 404, 429, 500])
def test_storage_failures_are_sanitized_and_not_retried(status):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(status, json={"secret": "do-not-expose"})

    client = storage(handler)
    with pytest.raises(IconStorageUnavailable) as exc:
        client.sign(f"{uuid4()}/{uuid4()}.png")
    assert "do-not-expose" not in str(exc.value)
    assert len(seen) == 1


def test_missing_key_and_timeout_fail_closed():
    seen = []

    def handler(req):
        seen.append(req)
        raise httpx.ReadTimeout("secret provider detail", request=req)

    path = f"{uuid4()}/{uuid4()}.png"
    with pytest.raises(IconStorageUnavailable):
        storage(handler, secret="").sign(path)
    assert not seen
    with pytest.raises(IconStorageUnavailable) as exc:
        storage(handler).sign(path)
    assert "secret provider detail" not in str(exc.value)
    assert len(seen) == 1


def test_storage_sign_rejects_noncanonical_path_before_network():
    seen = []
    client = storage(lambda req: seen.append(req) or httpx.Response(500))
    for path in ["../profile-private/secret", "https://evil.test/image", "profile-private/secret"]:
        with pytest.raises(InvalidIcon):
            client.sign(path)
    assert not seen


def test_jpeg_exif_is_removed():
    image = Image.new("RGB", (4, 4), "red")
    exif = image.getexif()
    exif[270] = "private-description-location"
    source = io.BytesIO()
    image.save(source, format="JPEG", exif=exif)
    cleaned = normalize_image(source.getvalue(), "image/jpeg")
    assert b"private-description-location" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as result:
        assert not result.getexif()


def test_normalized_file_must_also_fit_byte_limit(monkeypatch):
    import random

    from alter_domain import icons

    image = Image.frombytes("RGB", (32, 32), random.Random(0).randbytes(32 * 32 * 3))
    source = io.BytesIO()
    image.save(source, format="JPEG")
    assert len(source.getvalue()) < 2000
    monkeypatch.setattr(icons, "MAX_BYTES", 2000)
    with pytest.raises(InvalidIcon):
        icons.normalize_image(source.getvalue(), "image/jpeg")
