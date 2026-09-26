"""Daily limits for demo accounts, counted from the jobs they started in the last 24 hours.

Admin and user accounts have no limits. A job that failed, or was cancelled before it started, does not count.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, func, not_, select
from sqlalchemy.orm import Session

from app.models import Account, Job
from app.settings import Settings

WINDOW = timedelta(hours=24)

# Quota name, the job kind it counts, and how the refusal names one and several.
DAILY = {
    "runs": ("generate", "run", "runs"),
    "judge_cycles": ("evaluate", "judge cycle", "judge cycles"),
    "deep_searches": ("deep_search", "deep search", "deep searches"),
}


def limits(cfg: Settings) -> dict:
    return {
        "runs": cfg.demo_runs_per_day,
        "judge_cycles": cfg.demo_judge_cycles_per_day,
        "deep_searches": cfg.demo_deep_searches_per_day,
        "max_sequences": cfg.demo_max_sequences,
        "max_provider_calls": cfg.demo_max_provider_calls,
    }


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _counted(db: Session, account: Account, kind: str, since: datetime):
    return select(Job).where(
        Job.owner_id == account.id,
        Job.kind == kind,
        Job.created_at >= since,
        Job.status != "failed",
        not_(and_(Job.status == "cancelled", Job.started_at.is_(None))),
    )


def usage(db: Session, account: Account, cfg: Settings) -> dict | None:
    """What a demo account has used today and when each quota next frees a slot. None for other accounts."""
    if account.kind != "demo":
        return None
    since = datetime.now(timezone.utc) - WINDOW
    ceilings = limits(cfg)
    daily = {}
    for name, (kind, _, _) in DAILY.items():
        query = _counted(db, account, kind, since).subquery()
        used, oldest = db.execute(select(func.count(), func.min(query.c.created_at)).select_from(query)).one()
        daily[name] = {
            "used": int(used),
            "limit": ceilings[name],
            "frees_at": (_utc(oldest) + WINDOW).isoformat() if oldest is not None and used >= ceilings[name] else None,
        }
    return {"window_hours": 24, "daily": daily, "max_sequences": ceilings["max_sequences"], "max_provider_calls": ceilings["max_provider_calls"]}


def require_daily(db: Session, account: Account, cfg: Settings, name: str) -> None:
    """Refuse with 429 when a demo account has used up a daily quota."""
    report = usage(db, account, cfg)
    if report is None:
        return
    entry = report["daily"][name]
    if entry["used"] < entry["limit"]:
        return
    phrase = DAILY[name][1] if entry["limit"] == 1 else DAILY[name][2]
    when = f" The next one is available at {entry['frees_at'][11:16]} UTC." if entry["frees_at"] else ""
    raise HTTPException(status_code=429, detail=f"Demo accounts can start {entry['limit']} {phrase} a day.{when}")


def require_run_size(account: Account, cfg: Settings, sequences: int) -> None:
    if account.kind == "demo" and sequences > cfg.demo_max_sequences:
        raise HTTPException(
            status_code=422,
            detail=f"Demo runs hold at most {cfg.demo_max_sequences:,} sequences; this one asks for {sequences:,}.",
        )


def require_provider_calls(account: Account, cfg: Settings, calls: int) -> None:
    if account.kind == "demo" and calls > cfg.demo_max_provider_calls:
        raise HTTPException(status_code=422, detail=f"Demo runs make at most {cfg.demo_max_provider_calls} provider calls; this one may make {calls}.")
