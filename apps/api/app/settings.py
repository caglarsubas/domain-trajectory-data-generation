from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEV_JWT_SECRET = "dev-only-change-me"


class SettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    credential_master_key: str
    jwt_secret: str
    admin_email: str
    admin_password: str
    upload_dir: Path
    inference_base_url: str
    inference_api_key: str
    inference_tenant: str
    inference_org_id: str
    inference_key_id: str
    inference_judge_model: str
    dev_mode: bool


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[3]
    return Settings(
        database_url=os.environ.get("DATABASE_URL", f"sqlite:///{root / 'data' / 'traj.db'}"),
        credential_master_key=os.environ.get("CREDENTIAL_MASTER_KEY", ""),
        jwt_secret=os.environ.get("JWT_SECRET", "").strip() or DEV_JWT_SECRET,
        admin_email=os.environ.get("ADMIN_EMAIL", ""),
        admin_password=os.environ.get("ADMIN_PASSWORD", ""),
        upload_dir=Path(os.environ.get("UPLOAD_DIR", root / "data" / "uploads")),
        inference_base_url=os.environ.get("INFERENCE_ENGINE_BASE_URL", "").strip(),
        inference_api_key=os.environ.get("INFERENCE_ENGINE_API_KEY", "").strip(),
        inference_tenant=os.environ.get("INFERENCE_ENGINE_TENANT", "domain-trajectory-data-generation"),
        inference_org_id=os.environ.get("INFERENCE_ENGINE_ORG_ID", "org-trajdata"),
        # INFERENCE_ENGINE_API_KEY_ID is the older name some local .env files still use.
        inference_key_id=(
            os.environ.get("INFERENCE_ENGINE_KEY_ID")
            or os.environ.get("INFERENCE_ENGINE_API_KEY_ID")
            or "domain-trajectory-data-generation-primary"
        ).strip(),
        inference_judge_model=os.environ.get("INFERENCE_ENGINE_JUDGE_MODEL", "").strip() or "qwen3.8:27b",
        dev_mode=os.environ.get("TRAJ_DEV_MODE", "").strip().lower() in {"1", "true", "yes"},
    )


def check_startup(cfg: Settings) -> None:
    """Refuse to serve with settings that would be unsafe or silently broken."""
    from app.judge import EvalNotConfigured, normalize_base_url

    if cfg.jwt_secret == DEV_JWT_SECRET and not cfg.dev_mode:
        raise SettingsError("JWT_SECRET is not set. Set it, or set TRAJ_DEV_MODE=1 for local development.")
    if cfg.inference_base_url:
        try:
            normalize_base_url(cfg.inference_base_url)
        except EvalNotConfigured as exc:
            raise SettingsError(str(exc)) from exc
