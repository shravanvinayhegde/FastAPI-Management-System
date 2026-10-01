from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Prefer DATABASE_URL in hosted environments (Render, Railway, etc.).
    database_url: Optional[str] = None

    # Fallback split DB settings for local/dev usage.
    database_hostname: Optional[str] = None
    database_password: Optional[str] = None
    database_name: Optional[str] = None
    secret_key: str = "CHANGE_ME"
    database_port: Optional[str] = None
    database_username: Optional[str] = None
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Comma-separated list. Examples:
    # - "*" (allow all)
    # - "http://localhost:3000,https://myapp.com"
    cors_origins: Optional[str] = "https://voteflow-phi.vercel.app"

    # Local disk fallback when S3/R2 env vars are unset. Public contract remains /media/...
    media_directory: Path = Path("media")
    s3_bucket: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_BUCKET", "AWS_S3_BUCKET", "AWS_BUCKET"),
    )
    s3_region: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"),
    )
    s3_endpoint_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL"),
    )
    s3_access_key_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
    )
    s3_secret_access_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
    )
    # Optional public base URL (e.g. R2 custom domain); defaults to s3_endpoint_url
    s3_public_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_PUBLIC_BASE_URL", "AWS_PUBLIC_BASE_URL"),
    )
    max_image_upload_mb: int = 10
    max_video_upload_mb: int = 100
    max_image_dimension: int = 4096
    frontend_url: Optional[str] = None
    render: bool = Field(default=False, validation_alias=AliasChoices("RENDER"))

    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent / ".env")

    @field_validator(
        "s3_bucket",
        "s3_region",
        "s3_endpoint_url",
        "s3_access_key_id",
        "s3_secret_access_key",
        "s3_public_base_url",
        mode="before",
    )
    @classmethod
    def _clean_s3_value(cls, value):
        """Trim pasted S3 values and treat blank values as unset."""
        if value is None:
            return None
        value = str(value).strip().strip("\"'")
        return value or None

    @field_validator("s3_endpoint_url", "s3_public_base_url", mode="after")
    @classmethod
    def _normalise_url(cls, value):
        if not value:
            return value
        if not value.lower().startswith(("http://", "https://")):
            value = f"https://{value}"
        return value.rstrip("/")

    @property
    def cors_origins_list(self) -> List[str]:
        if not self.cors_origins:
            return ["*"]
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()

if os.getenv("RENDER") and settings.secret_key == "CHANGE_ME":
    raise RuntimeError(
        "secret_key is still the default 'CHANGE_ME' value while running on Render. "
        "Set the SECRET_KEY environment variable to a strong random value before deploying."
    )