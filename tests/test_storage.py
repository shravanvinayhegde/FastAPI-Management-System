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
