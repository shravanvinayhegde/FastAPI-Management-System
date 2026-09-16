from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Prefer DATABASE_URL in hosted environments (Render, Railway, etc.).
    database_url: Optional[str] = None

    # Fallback split DB settings for local/dev usage.
    database_hostname: Optional[str] = None
    database_password: Optional[str] = None
    database_name: Optional[str] = None
    secret_key: str
    database_port: Optional[str] = None
    database_username: Optional[str] = None
    algorithm: str
    access_token_expire_minutes: int

    # Comma-separated list. Examples:
    # - "*" (allow all)
    # - "http://localhost:3000,https://myapp.com"
    cors_origins: Optional[str] = None

    # Local disk fallback when S3/R2 env vars are unset. Public contract remains /media/...
    media_directory: Path = Path("media")
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    # Optional public base URL (e.g. R2 custom domain); defaults to s3_endpoint_url
    s3_public_base_url: Optional[str] = None
    max_image_upload_mb: int = 10
    max_video_upload_mb: int = 100
    max_image_dimension: int = 4096
    frontend_url: Optional[str] = None

    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent / ".env")

    @property
    def cors_origins_list(self) -> List[str]:
        if not self.cors_origins:
            return ["*"]
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()