from __future__ import annotations

import logging
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger("voteflow.storage")


class StorageError(RuntimeError):
    pass


def uses_object_storage() -> bool:
    return bool(
        settings.s3_bucket
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
    )


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
            s3={"addressing_style": "path"},
        ),
    )


def save_bytes(key: str, data: bytes, content_type: str) -> None:
    if uses_object_storage():
        try:
            _client().put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception(
                "S3 upload failed: bucket=%s key=%s content_type=%s",
                settings.s3_bucket,
                key,
                content_type,
            )
            raise StorageError("Object storage upload failed") from exc
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
                raise StorageError("Object storage deletion failed") from exc
        except BotoCoreError as exc:
            logger.exception(
                "S3 delete failed: bucket=%s key=%s",
                settings.s3_bucket,
                key,
            )
            raise StorageError("Object storage deletion failed") from exc


def public_url(key: str) -> str:
    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{key}"

    if settings.s3_endpoint_url:
        return f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{key}"

    if settings.s3_bucket:
        region = settings.s3_region or "us-east-1"
        return f"https://{settings.s3_bucket}.s3.{region}.amazonaws.com/{key}"

    return f"/media/{key}"
