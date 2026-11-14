"""Server-only immutable icon uploads. Never accepts a caller-supplied URL or bucket."""

import hashlib
import io
import re
import warnings
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import Connection, text

from alter_domain.settings import Settings

ICON_BUCKET = "agent-icons"
PRIVATE_BUCKET = "profile-private"
MAX_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 2048
URL_TTL_SECONDS = 300
MIME_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
PATH_PATTERN = r"^[0-9a-f-]{36}/[0-9a-f-]{36}\.png$"


class InvalidIcon(Exception):
    pass


class IconStorageUnavailable(Exception):
    pass


def normalize_image(data: bytes, content_type: str) -> bytes:
    if content_type not in MIME_FORMATS or not 0 < len(data) <= MAX_BYTES:
        raise InvalidIcon()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.format != MIME_FORMATS[content_type]
                    or not 0 < image.width <= MAX_DIMENSION
                    or not 0 < image.height <= MAX_DIMENSION
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise InvalidIcon()
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                # Fresh pixels remove EXIF, GPS, XMP, comments and trailing/polyglot bytes.
                clean = Image.new("RGBA", image.size)
                clean.paste(image.convert("RGBA"))
                output = io.BytesIO()
                clean.save(output, format="PNG")
        result = output.getvalue()
        if len(result) > MAX_BYTES:
            raise InvalidIcon()
        return result
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise InvalidIcon() from None


def validate_path(path: str, owner: UUID) -> None:
    if not re.fullmatch(PATH_PATTERN, path):
        raise InvalidIcon()
    prefix, filename = path.split("/")
    try:
        if str(UUID(prefix)) != prefix or UUID(prefix) != owner:
            raise InvalidIcon()
        if str(UUID(filename[:-4])) != filename[:-4]:
            raise InvalidIcon()
    except ValueError:
        raise InvalidIcon() from None


def require_owned_icon(conn: Connection, owner: UUID, path: str) -> None:
    validate_path(path, owner)
    # Metadata is written only by this API after decoding/re-encoding, never by a browser.
    exists = conn.execute(
        text(
            "select 1 from public.agent_icon_uploads where path=:path and owner_id=:owner "
            "and content_type='image/png' and size_bytes between 1 and :limit"
        ),
        {"path": path, "owner": owner, "limit": MAX_BYTES},
    ).first()
    if not exists:
        raise InvalidIcon()


class IconStorage:
    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        self.base = settings.supabase_url.rstrip("/") + "/storage/v1"
        self.secret = settings.supabase_storage_secret_key.get_secret_value()
        self.http = http

    def request(
        self, path: str, *, content: bytes | None = None, json_body: dict[str, int] | None = None
    ) -> httpx.Response:
        if not self.secret:
            raise IconStorageUnavailable()
        try:
            # The injected transport is shared with Auth; no redirects or retries.
            response = self.http.post(
                self.base + path,
                headers={
                    "apikey": self.secret,
                    "Authorization": f"Bearer {self.secret}",
                    "Content-Type": "image/png" if content is not None else "application/json",
                    "x-upsert": "false",
                },
                timeout=10,
                follow_redirects=False,
                content=content,
                json=json_body,
            )
            if response.status_code not in (200, 201):
                raise IconStorageUnavailable()
            return response
        except httpx.HTTPError:
            # Never expose provider bodies, headers, keys or signed tokens in errors/logs.
            raise IconStorageUnavailable() from None

    def upload(self, owner: UUID, normalized: bytes) -> str:
        path = f"{owner}/{uuid4()}.png"
        self.request(f"/object/{ICON_BUCKET}/{path}", content=normalized)
        return path

    def sign(self, path: str) -> str:
        try:
            validate_path(path, UUID(path.split("/", 1)[0]))
        except ValueError:
            raise InvalidIcon() from None
        response = self.request(
            f"/object/sign/{ICON_BUCKET}/{path}", json_body={"expiresIn": URL_TTL_SECONDS}
        )
        expected = f"/object/sign/{ICON_BUCKET}/{path}"
        try:
            signed = response.json()["signedURL"]
            if not isinstance(signed, str):
                raise ValueError
            parsed = urlsplit(signed)
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.fragment
                or parsed.path != expected
                or not parse_qs(parsed.query).get("token")
            ):
                raise ValueError
            return self.base + signed
        except (ValueError, KeyError, TypeError):
            raise IconStorageUnavailable() from None


def register_upload(
    conn: Connection, owner: UUID, storage: IconStorage, data: bytes, content_type: str
) -> str:
    normalized = normalize_image(data, content_type)
    path = storage.upload(owner, normalized)
    conn.execute(
        text(
            "insert into public.agent_icon_uploads (path,owner_id,content_type,size_bytes,sha256) "
            "values (:path,:owner,'image/png',:size,:sha)"
        ),
        {
            "path": path,
            "owner": owner,
            "size": len(normalized),
            "sha": hashlib.sha256(normalized).hexdigest(),
        },
    )
    return path
