from __future__ import annotations

import logging
import uuid
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger("voteflow.storage")

PRESIGNED_READ_SECONDS = 3600

ERROR_HINTS = {
    "InvalidAccessKeyId": (
        "S3_ACCESS_KEY_ID is wrong or revoked. For Neon use the credential's "
        "token_id (nak_live_...), not token_id_short."
    ),
    "SignatureDoesNotMatch": (
        "S3_SECRET_ACCESS_KEY is wrong. For Neon use s3_secret_access_key "
        "(nsk_live_... / 64-char hex), not the api_token. Re-create the credential if lost."
    ),
    "AccessDenied": (
        "The credential cannot write here. Create it with BOTH storage:read and "
        "storage:write scopes, on the same Neon branch as S3_ENDPOINT_URL."
    ),
    "NoSuchBucket": (
        "The bucket named in S3_BUCKET does not exist on this branch. Create it in "
        "Neon Console -> your branch -> Object storage, and check the spelling."
    ),
    "403": (
        "Access denied. Usually a wrong S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY, a credential "
        "without storage:read + storage:write, or one issued on a different Neon branch "
        "than S3_ENDPOINT_URL."
    ),
    "404": "Bucket or endpoint not found. Check S3_BUCKET and S3_ENDPOINT_URL.",
    "NotImplemented": (
        "The storage service rejected a header sent by the SDK (usually a checksum "
        "header). This build already disables it; make sure the new code is deployed."
    ),
    "SlowDown": "Storage rate limit hit. Retry in a moment.",
    "EntityTooLarge": "The file is bigger than the storage service allows.",
    "RequestTimeTooSkewed": "The server clock is out of sync with the storage service.",
    "EndpointConnectionError": (
        "Cannot reach S3_ENDPOINT_URL. It must be the full branch storage endpoint, "
        "e.g. https://br-xxxx.storage.c-2.us-east-2.aws.neon.tech"
    ),
    "InvalidEndpoint": "S3_ENDPOINT_URL is not a valid URL.",
}


class StorageError(RuntimeError):
    def __init__(self, message: str, code: str = "StorageError", hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def uses_object_storage() -> bool:
    return bool(
        settings.s3_bucket
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
    )


def validate_configuration() -> None:
    configured_values = {
        "S3_BUCKET": settings.s3_bucket,
        "S3_ACCESS_KEY_ID": settings.s3_access_key_id,
        "S3_SECRET_ACCESS_KEY": settings.s3_secret_access_key,
        "S3_ENDPOINT_URL": settings.s3_endpoint_url,
        "S3_REGION": settings.s3_region,
    }
    if not any(configured_values.values()):
        return

    missing = [name for name, value in configured_values.items() if not value]
    if missing:
        message = "Incomplete object-storage configuration; missing: " + ", ".join(missing)
        logger.error(message)
        if settings.render:
            raise RuntimeError(message)


@lru_cache
def _client():
    logger.info(
        "S3 configuration: bucket_configured=%s access_key_configured=%s "
        "secret_configured=%s endpoint_configured=%s region_configured=%s "
        "bucket=%s region=%s endpoint=%s",
        bool(settings.s3_bucket),
        bool(settings.s3_access_key_id),
        bool(settings.s3_secret_access_key),
        bool(settings.s3_endpoint_url),
        bool(settings.s3_region),
        settings.s3_bucket or "<unset>",
        settings.s3_region or "us-east-2",
        settings.s3_endpoint_url or "<AWS default>",
    )
    return boto3.client(
        "s3",
        region_name=settings.s3_region or "us-east-2",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _to_storage_error(exc: Exception, action: str) -> StorageError:
    code = "StorageError"
    status = None
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "StorageError")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    elif isinstance(exc, BotoCoreError):
        code = type(exc).__name__
    elif isinstance(exc, ValueError):
        code = "InvalidEndpoint"

    hint = ERROR_HINTS.get(code) or ERROR_HINTS.get(str(status), "")
    message = f"Object storage {action} failed: {code}" + (f" (HTTP {status})" if status else "")
    return StorageError(message, code=code, hint=hint)


def save_bytes(key: str, data: bytes, content_type: str) -> None:
    if uses_object_storage():
        try:
            _client().put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            error = _to_storage_error(exc, "upload")
            logger.error(
                "S3 upload failed: code=%s bucket=%s key=%s content_type=%s size=%d "
                "endpoint=%s hint=%s raw=%s",
                error.code,
                settings.s3_bucket,
                key,
                content_type,
                len(data),
                settings.s3_endpoint_url or "<AWS default>",
                error.hint or "-",
                exc,
            )
            raise error from exc
        return

    path = settings.media_directory / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def delete_bytes(key: str) -> None:
    local_path = settings.media_directory / key
    if local_path.exists():
        local_path.unlink()
        return

    if uses_object_storage():
        try:
            _client().delete_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code not in {"404", "NoSuchKey", "NotFound"}:
                logger.exception(
                    "S3 delete failed: bucket=%s key=%s",
                    settings.s3_bucket,
                    key,
                )
                raise _to_storage_error(exc, "deletion") from exc
        except (BotoCoreError, ValueError) as exc:
            logger.exception(
                "S3 delete failed: bucket=%s key=%s",
                settings.s3_bucket,
                key,
            )
            raise _to_storage_error(exc, "deletion") from exc


def public_url(key: str) -> str:
    """Permanent public URL for an object."""
    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{key}"

    if settings.s3_endpoint_url:
        return f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{key}"

    if settings.s3_bucket:
        region = settings.s3_region or "us-east-1"
        return f"https://{settings.s3_bucket}.s3.{region}.amazonaws.com/{key}"

    return f"/media/{key}"


def media_url(key: str) -> str:
    if settings.s3_public_base_url:
        return public_url(key)

    if uses_object_storage():
        try:
            return _client().generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.s3_bucket, "Key": key},
                ExpiresIn=PRESIGNED_READ_SECONDS,
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            error = _to_storage_error(exc, "signing")
            logger.error("S3 presign failed: code=%s key=%s raw=%s", error.code, key, exc)
            raise error from exc

    return f"/media/{key}"


def diagnose() -> dict:
    report: dict = {
        "object_storage_enabled": uses_object_storage(),
        "bucket": settings.s3_bucket,
        "region": settings.s3_region or "us-east-2 (default)",
        "endpoint": settings.s3_endpoint_url or "<AWS default>",
        "public_base_url": settings.s3_public_base_url,
        "access_key_id_prefix": (settings.s3_access_key_id or "")[:9],
        "access_key_id_length": len(settings.s3_access_key_id or ""),
        "secret_length": len(settings.s3_secret_access_key or ""),
        "steps": [],
        "ok": False,
    }
    if not uses_object_storage():
        report["error"] = (
            "S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must all be set. "
            "Until they are, files are saved to local disk (lost on every Render deploy)."
        )
        return report

    key = f"healthcheck/{uuid.uuid4().hex}.txt"
    steps = [
        ("head_bucket", lambda c: c.head_bucket(Bucket=settings.s3_bucket)),
        (
            "put_object",
            lambda c: c.put_object(
                Bucket=settings.s3_bucket, Key=key, Body=b"ok", ContentType="text/plain"
            ),
        ),
        ("get_object", lambda c: c.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()),
        ("delete_object", lambda c: c.delete_object(Bucket=settings.s3_bucket, Key=key)),
    ]
    try:
        client = _client()
    except (BotoCoreError, ValueError) as exc:
        error = _to_storage_error(exc, "client setup")
        report["steps"].append(
            {"step": "create_client", "ok": False, "error_code": error.code, "hint": error.hint}
        )
        return report

    for name, call in steps:
        try:
            call(client)
            report["steps"].append({"step": name, "ok": True})
        except (BotoCoreError, ClientError, ValueError) as exc:
            error = _to_storage_error(exc, name)
            if name == "head_bucket" and error.code == "404":
                error.code, error.hint = "NoSuchBucket", ERROR_HINTS["NoSuchBucket"]
            report["steps"].append(
                {"step": name, "ok": False, "error_code": error.code, "hint": error.hint}
            )
            if name == "get_object":
                try:
                    client.delete_object(Bucket=settings.s3_bucket, Key=key)
                except Exception:
                    pass
            return report

    report["ok"] = True
    return report
