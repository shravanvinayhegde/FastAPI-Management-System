from __future__ import annotations

from functools import lru_cache

import boto3

from app.config import settings


def uses_object_storage() -> bool:
    return bool(
        settings.s3_bucket
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
    )


@lru_cache
def _client():
    return boto3.client(
        "s3",
        region_name=settings.s3_region or "auto",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
    )


def save_bytes(key: str, data: bytes, content_type: str) -> None:
    if uses_object_storage():
        _client().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return
    path = settings.media_directory / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def delete_bytes(key: str) -> None:
    if uses_object_storage():
        _client().delete_object(Bucket=settings.s3_bucket, Key=key)
        return
    (settings.media_directory / key).unlink(missing_ok=True)


def public_url(key: str) -> str:
    base = (settings.s3_public_base_url or settings.s3_endpoint_url or "").rstrip("/")
    return f"{base}/{settings.s3_bucket}/{key}"
