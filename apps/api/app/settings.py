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
    demo_runs_per_day: int = 10
    demo_max_sequences: int = 2000
    demo_judge_cycles_per_day: int = 10
    demo_deep_searches_per_day: int = 3
    second_judge_model: str = "gemma4:26b"
    judge_sample_size: int = 6
    judge_prompt_tokens: int = 8000
    judge_reference_chars: int = 6000


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
        demo_runs_per_day=_count("DEMO_RUNS_PER_DAY", 10),
        demo_max_sequences=_count("DEMO_MAX_SEQUENCES", 2000),
        demo_judge_cycles_per_day=_count("DEMO_JUDGE_CYCLES_PER_DAY", 10),
        demo_deep_searches_per_day=_count("DEMO_DEEP_SEARCHES_PER_DAY", 3),
        # The second opinion; set it empty to judge with the primary model alone.
        second_judge_model=os.environ.get("INFERENCE_ENGINE_SECOND_JUDGE_MODEL", "gemma4:26b").strip(),
        judge_sample_size=max(_count("JUDGE_SAMPLE_SIZE", 6), 1),
        # Ollama serves a 32,768-token window whatever the model was trained on; the prompt stays well inside it.
        judge_prompt_tokens=min(max(_count("JUDGE_PROMPT_TOKENS", 8000), 1000), 30000),
        # How much retrieved warm-start text the judge's brief carries.
        judge_reference_chars=min(max(_count("JUDGE_REFERENCE_CHARS", 6000), 500), 40000),
    )


def _count(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(int(raw), 0)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a whole number") from exc


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
