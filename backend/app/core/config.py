"""Application settings, loaded from environment variables (and `.env` in development).

Production refuses to start with missing or development-default secrets.
"""

from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEV_SECRET_PREFIX = "dev-insecure-"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    env: Literal["development", "test", "production"] = "development"

    # --- database -----------------------------------------------------------------------------
    # Runtime connection: must use APP_DB_ROLE (no BYPASSRLS, owns no tables).
    database_url: str = "postgresql+asyncpg://restaurant_app@127.0.0.1:5432/restaurant"
    # Migration connection: the role that owns the schema.
    migration_database_url: str | None = None
    app_db_role: str = "restaurant_app"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)

    # --- staff auth -----------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(DEV_SECRET_PREFIX + "jwt-secret-change-me-0123456789")
    jwt_issuer: str = "restaurant-api"
    jwt_audience: str = "restaurant-staff"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    auth_session_absolute_days: int = Field(default=30, ge=1, le=90)
    refresh_token_idle_days: int = Field(default=14, ge=1, le=90)
    refresh_reuse_grace_seconds: int = Field(default=30, ge=0, le=120)
    password_reset_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    # The reset page is part of the customer web app (same origin as the API).
    password_reset_url: str = "http://localhost:5173/reset-password"

    # Argon2id (RFC 9106 low-memory profile by default). Concurrency is bounded because every
    # hash allocates `argon2_memory_kib` of RAM.
    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_kib: int = Field(default=65536, ge=1024, le=1048576)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)
    argon2_max_concurrency: int = Field(default=4, ge=1, le=64)

    # --- restaurant registration ----------------------------------------------------------------
    # closed: only operators (CLI); invite: requires one of REGISTRATION_INVITE_CODES; open: anyone.
    registration_mode: Literal["closed", "invite", "open"] = "invite"
    registration_invite_codes: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)

    # --- tables, QR, table PIN, customer sessions ----------------------------------------------
    # QR codes encode {PUBLIC_ORDER_BASE_URL}/t/{token}; the customer web app is served there.
    public_order_base_url: str = "http://localhost:5173"
    max_tables_per_restaurant: int = Field(default=300, ge=1, le=5000)
    pin_hmac_key: SecretStr = SecretStr(DEV_SECRET_PREFIX + "pin-hmac-key-change-me-0123456789")
    pin_enc_key: SecretStr = SecretStr(DEV_SECRET_PREFIX + "pin-enc-key-change-me-0123456789")
    pin_scheduler_enabled: bool = True
    pin_scheduler_interval_seconds: int = Field(default=60, ge=5, le=3600)

    # --- data retention -------------------------------------------------------------------------
    # A background job deletes records nobody needs any more. Orders are kept (they are the
    # restaurant's own history and carry no personal data by design).
    retention_enabled: bool = True
    retention_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    # Staff sign-in sessions (and their refresh tokens) and password-reset links, counted from
    # when they stopped working.
    auth_record_retention_days: int = Field(default=30, ge=1, le=365)
    # Customer table sessions that never placed an order (sessions with orders are kept with them).
    table_session_retention_days: int = Field(default=30, ge=1, le=365)
    # The database itself refuses to purge anything younger than 180 days.
    audit_log_retention_days: int = Field(default=365, ge=180, le=3650)
    # Staff problem reports (free text, may mention guests): kept long enough to learn from.
    problem_report_retention_days: int = Field(default=365, ge=30, le=3650)

    # --- pilot usage counts ---------------------------------------------------------------------
    # Daily per-restaurant counts (QR opened, PIN accepted/wrong/locked, staff connections) are
    # gathered in memory and written every USAGE_FLUSH_SECONDS. Counts only, no personal data.
    usage_counters_enabled: bool = True
    usage_flush_seconds: int = Field(default=60, ge=5, le=3600)

    # --- menu images ----------------------------------------------------------------------------
    # Uploaded files live outside the application code. In production point MEDIA_ROOT at a
    # persistent volume (or serve /media from a CDN/reverse proxy).
    media_root: Path = Path(__file__).resolve().parents[2] / ".media"
    media_url_prefix: str = "/media"
    max_image_upload_bytes: int = Field(default=5 * 1_048_576, ge=100_000, le=20 * 1_048_576)

    # --- infrastructure -------------------------------------------------------------------------
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    # Live updates to staff devices. Several API workers need the redis bus to share events.
    event_bus_backend: Literal["memory", "redis"] = "memory"
    ws_auth_timeout_seconds: float = Field(default=5, gt=0, le=60)
    ws_recheck_seconds: float = Field(default=60, gt=0, le=600)
    ws_max_connections_per_user: int = Field(default=5, ge=1, le=50)
    redis_url: str | None = None
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    public_web_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_body_bytes: int = Field(default=1_048_576, ge=1024, le=20 * 1_048_576)

    mail_backend: Literal["console", "memory", "smtp"] = "console"
    mail_from: str = "no-reply@example.com"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True

    log_level: str = "INFO"

    @field_validator(
        "trusted_proxies",
        "cors_origins",
        "public_web_origins",
        "registration_invite_codes",
        mode="before",
    )
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("media_url_prefix")
    @classmethod
    def _media_prefix(cls, value: str) -> str:
        if not re.fullmatch(r"/[a-z0-9-]+", value):
            raise ValueError("media_url_prefix must look like /media")
        return value

    @field_validator("public_order_base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?", value):
            raise ValueError("public_order_base_url must be an http(s) URL without query")
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_networks(cls, value: list[str]) -> list[str]:
        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value

    @field_validator("app_db_role")
    @classmethod
    def _valid_role(cls, value: str) -> str:
        if not _ROLE_NAME.fullmatch(value):
            raise ValueError("app_db_role must be a simple lowercase identifier")
        return value

    @field_validator("jwt_secret", "pin_hmac_key", "pin_enc_key")
    @classmethod
    def _long_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("secrets must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.env != "production":
            return self
        problems: list[str] = []
        secrets = {
            "JWT_SECRET": self.jwt_secret,
            "PIN_HMAC_KEY": self.pin_hmac_key,
            "PIN_ENC_KEY": self.pin_enc_key,
        }
        for name, secret in secrets.items():
            if secret.get_secret_value().startswith(DEV_SECRET_PREFIX):
                problems.append(f"{name} uses a development default")
        values = [s.get_secret_value() for s in secrets.values()]
        if len(set(values)) != len(values):
            problems.append("JWT_SECRET, PIN_HMAC_KEY and PIN_ENC_KEY must all differ")
        if self.rate_limit_backend != "redis" or not self.redis_url:
            problems.append("production requires RATE_LIMIT_BACKEND=redis and REDIS_URL")
        if self.event_bus_backend != "redis":
            problems.append("production requires EVENT_BUS_BACKEND=redis (shared live updates)")
        if self.mail_backend != "smtp" or not self.smtp_host:
            problems.append("production requires MAIL_BACKEND=smtp and SMTP_HOST")
        if not self.password_reset_url.startswith("https://"):
            problems.append("PASSWORD_RESET_URL must be https in production")
        if self.argon2_memory_kib < 19456 or self.argon2_time_cost < 2:
            problems.append("Argon2 parameters below OWASP minimum (m>=19456 KiB, t>=2)")
        if not self.public_order_base_url.startswith("https://"):
            problems.append("PUBLIC_ORDER_BASE_URL must be https in production")
        if not self.public_web_origins:
            problems.append("PUBLIC_WEB_ORIGINS must list the customer web origin(s)")
        if self.registration_mode == "invite" and (
            not self.registration_invite_codes
            or any(len(c.get_secret_value()) < 16 for c in self.registration_invite_codes)
        ):
            problems.append("REGISTRATION_INVITE_CODES must contain codes of 16+ characters")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
