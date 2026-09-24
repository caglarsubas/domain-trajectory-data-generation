from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[3]
    return Settings(
        database_url=os.environ.get("DATABASE_URL", f"sqlite:///{root / 'data' / 'traj.db'}"),
        credential_master_key=os.environ.get("CREDENTIAL_MASTER_KEY", ""),
        jwt_secret=os.environ.get("JWT_SECRET", "dev-only-change-me"),
        admin_email=os.environ.get("ADMIN_EMAIL", ""),
        admin_password=os.environ.get("ADMIN_PASSWORD", ""),
        upload_dir=Path(os.environ.get("UPLOAD_DIR", root / "data" / "uploads")),
        inference_base_url=os.environ.get("INFERENCE_ENGINE_BASE_URL", "").strip(),
        inference_api_key=os.environ.get("INFERENCE_ENGINE_API_KEY", "").strip(),
        inference_tenant=os.environ.get("INFERENCE_ENGINE_TENANT", "domain-trajectory-data-generation"),
        inference_org_id=os.environ.get("INFERENCE_ENGINE_ORG_ID", "org-trajdata"),
        inference_key_id=os.environ.get("INFERENCE_ENGINE_KEY_ID", "domain-trajectory-data-generation-primary"),
    )
