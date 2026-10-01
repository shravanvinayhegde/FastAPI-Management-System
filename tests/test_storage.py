from pathlib import Path
from unittest.mock import Mock

from botocore.exceptions import ClientError

from app import storage
from app.config import settings


def test_s3_client_uses_path_style_and_configured_region(monkeypatch):
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(storage.boto3, "client", factory)
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")
    monkeypatch.setattr(settings, "s3_region", "us-east-2")
    monkeypatch.setattr(settings, "s3_endpoint_url", "https://storage.example")
    monkeypatch.setattr(settings, "s3_access_key_id", "access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret")
    storage._client.cache_clear()

    try:
        storage._client()
    finally:
        storage._client.cache_clear()

    kwargs = factory.call_args.kwargs
    assert kwargs["region_name"] == "us-east-2"
    assert kwargs["config"].s3["addressing_style"] == "path"
    assert kwargs["endpoint_url"] == "https://storage.example"


def test_save_bytes_uses_neon_object_storage_put_object(monkeypatch):
    client = Mock()
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")
    monkeypatch.setattr(settings, "s3_access_key_id", "access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret")

    storage.save_bytes("posts/example.jpg", b"image", "image/jpeg")

    client.put_object.assert_called_once_with(
        Bucket="voteflow-media",
        Key="posts/example.jpg",
        Body=b"image",
        ContentType="image/jpeg",
    )


def test_delete_bytes_ignores_missing_remote_object(monkeypatch, tmp_path: Path):
    client = Mock()
    client.delete_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "DeleteObject"
    )
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.setattr(settings, "media_directory", tmp_path)
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")
    monkeypatch.setattr(settings, "s3_access_key_id", "access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret")

    storage.delete_bytes("posts/missing.jpg")


def test_public_url_preserves_media_contract(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_base_url", None)
    monkeypatch.setattr(settings, "s3_endpoint_url", "https://storage.example")
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")

    assert storage.public_url("posts/example.jpg") == (
        "https://storage.example/voteflow-media/posts/example.jpg"
    )


def test_s3_client_disables_default_checksums(monkeypatch):
    factory = Mock(return_value=Mock())
    monkeypatch.setattr(storage.boto3, "client", factory)
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")
    monkeypatch.setattr(settings, "s3_access_key_id", "access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret")
    storage._client.cache_clear()
    try:
        storage._client()
    finally:
        storage._client.cache_clear()

    config = factory.call_args.kwargs["config"]
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"
    assert config.signature_version == "s3v4"


def test_s3_settings_are_trimmed_and_normalised():
    from app.config import Settings

    cfg = Settings(
        S3_BUCKET=" media\n",
        S3_ACCESS_KEY_ID='"nak_live_abc" ',
        S3_SECRET_ACCESS_KEY="secret\n",
        S3_ENDPOINT_URL="br-x.storage.c-2.us-east-2.aws.neon.tech/ ",
        S3_REGION="  ",
    )
    assert cfg.s3_bucket == "media"
    assert cfg.s3_access_key_id == "nak_live_abc"
    assert cfg.s3_secret_access_key == "secret"
    assert cfg.s3_endpoint_url == "https://br-x.storage.c-2.us-east-2.aws.neon.tech"
    assert cfg.s3_region is None


def test_save_bytes_reports_s3_error_code_and_hint(monkeypatch):
    client = Mock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
        "PutObject",
    )
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")
    monkeypatch.setattr(settings, "s3_access_key_id", "access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret")

    try:
        storage.save_bytes("posts/x.jpg", b"img", "image/jpeg")
    except storage.StorageError as exc:
        assert exc.code == "AccessDenied"
        assert "storage:write" in exc.hint
    else:
        raise AssertionError("StorageError was not raised")


def test_invalid_endpoint_becomes_storage_error(monkeypatch):
    def boom():
        raise ValueError("Invalid endpoint: nonsense")

    monkeypatch.setattr(storage, "_client", boom)
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")
    monkeypatch.setattr(settings, "s3_access_key_id", "access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret")

    try:
        storage.save_bytes("posts/x.jpg", b"img", "image/jpeg")
    except storage.StorageError as exc:
        assert exc.code == "InvalidEndpoint"
    else:
        raise AssertionError("StorageError was not raised")


def test_media_url_signs_private_buckets_and_prefers_public_base(monkeypatch):
    client = Mock()
    client.generate_presigned_url.return_value = "https://signed.example/x?X-Amz-Signature=1"
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.setattr(settings, "s3_bucket", "voteflow-media")
    monkeypatch.setattr(settings, "s3_access_key_id", "access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret")

    monkeypatch.setattr(settings, "s3_public_base_url", None)
    assert storage.media_url("posts/a.jpg").startswith("https://signed.example/")
    client.generate_presigned_url.assert_called_once()

    monkeypatch.setattr(settings, "s3_public_base_url", "https://cdn.example/voteflow-media")
    assert storage.media_url("posts/a.jpg") == "https://cdn.example/voteflow-media/posts/a.jpg"
