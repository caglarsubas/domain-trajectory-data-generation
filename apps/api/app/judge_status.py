"""Whether the judge can run, checked before a cycle is queued (decision 24).

Three questions, in order: does the engine answer, does its rubric registry, and are the judge models among the ones
it lists. A cycle, a regeneration, or a rubric proposal that would fail on any of them is refused with the reason
instead of queued. The engine does not say which models it serves for one another, so the study's last cycle says
whether its two judges were served by one model.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import runtime
from app.judge import EvalNotConfigured, normalize_base_url
from app.judging import judge_models
from app.models import EvalCycle, Run
from app.settings import Settings

CHECK_SECONDS = 5.0
# A ready judge is checked again after a minute; one that is not, after ten seconds, so a started engine shows soon.
READY_FOR = 60.0
NOT_READY_FOR = 10.0
LABELS = {"engine": "Engine", "rubrics": "Rubric registry", "models": "Judge models"}

_cache: dict[str, tuple[float, dict]] = {}


def forget() -> None:
    _cache.clear()


def status(cfg: Settings, *, fresh: bool = False) -> dict:
    """The judge's readiness, from a recent check when there is one."""
    models = judge_models(cfg)
    if runtime.judge is not None:
        own = getattr(runtime.judge, "status", None)
        if own is not None:
            return own()
        return _result({name: (True, "A judge is installed in this process.") for name in LABELS}, models)
    key = hashlib.sha256("|".join([cfg.inference_base_url, cfg.inference_api_key, *models]).encode()).hexdigest()
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and not fresh and now - hit[0] < (READY_FOR if hit[1]["ready"] else NOT_READY_FOR):
        return hit[1]
    value = check(cfg, transport=runtime.engine_transport)
    _cache.clear()
    _cache[key] = (now, value)
    return value


def require_ready(cfg: Settings) -> None:
    """Refuse, with the reason, work that would ask a judge that cannot answer."""
    found = status(cfg)
    if not found["ready"]:
        raise HTTPException(status_code=503, detail=found["reason"])


def check(cfg: Settings, transport: httpx.BaseTransport | None = None) -> dict:
    models = judge_models(cfg)
    missing = "INFERENCE_ENGINE_API_KEY" if not cfg.inference_api_key else "INFERENCE_ENGINE_BASE_URL" if not cfg.inference_base_url else ""
    if missing:
        return _result({"engine": (False, f"The platform judge is not configured on this server: {missing} is missing.")}, models)
    try:
        origin = normalize_base_url(cfg.inference_base_url)
    except EvalNotConfigured as exc:
        return _result({"engine": (False, str(exc))}, models)
    headers = {"Authorization": f"Bearer {cfg.inference_api_key}"}
    with httpx.Client(base_url=origin, transport=transport, timeout=CHECK_SECONDS) as client:
        found = {"engine": _engine(client)}
        if found["engine"][0]:
            found["rubrics"] = _rubrics(client, headers)
            found["models"] = _models(client, headers, models)
    return _result(found, models)


def _get(client: httpx.Client, path: str, headers: dict | None = None) -> httpx.Response | str:
    """The response, or why there was none."""
    try:
        return client.get(path, headers=headers or {})
    except httpx.TimeoutException:
        return f"The engine at INFERENCE_ENGINE_BASE_URL did not answer within {CHECK_SECONDS:g} seconds."
    except httpx.HTTPError:
        return "No engine answered at INFERENCE_ENGINE_BASE_URL. Start llm_inference_engine there, or point the address at one that runs."


def _json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _engine(client: httpx.Client) -> tuple[bool, str]:
    response = _get(client, "/v1/health")
    if isinstance(response, str):
        return False, response
    if not response.is_success:
        # An offline ngrok tunnel answers every path with 404 while its address still resolves.
        return False, (
            f"Something answers at INFERENCE_ENGINE_BASE_URL, but not llm_inference_engine: its health check answered "
            f"{response.status_code}. An offline tunnel answers this way."
        )
    health = _json(response)
    if health.get("ready") is False:
        message = (health.get("readiness") or {}).get("message") or "not ready yet"
        return False, f"The engine is starting: {message}."
    version = health.get("version")
    return True, f"Engine {version} is ready." if version else "The engine is ready."


def _rejected(response: httpx.Response) -> str | None:
    if response.status_code in {401, 403}:
        return "The engine rejected the platform key. Check INFERENCE_ENGINE_API_KEY."
    return None


def _rubrics(client: httpx.Client, headers: dict) -> tuple[bool, str]:
    response = _get(client, "/v1/evals/rubrics", headers)
    if isinstance(response, str):
        return False, response
    if reason := _rejected(response):
        return False, reason
    if response.status_code in {404, 405}:
        return False, "The engine has no rubric registry. The judge needs llm_inference_engine with tenant rubrics."
    if not response.is_success:
        return False, f"The engine's rubric registry answered {response.status_code}."
    listed = _json(response).get("data")
    if not isinstance(listed, list):
        return False, "The engine's rubric registry answered with something the studio could not read."
    return True, f"{len(listed)} rubrics listed."


def _models(client: httpx.Client, headers: dict, wanted: list[str]) -> tuple[bool, str]:
    response = _get(client, "/v1/models", headers)
    if isinstance(response, str):
        return False, response
    if reason := _rejected(response):
        return False, reason
    if not response.is_success:
        return False, f"The engine's model list answered {response.status_code}."
    payload = _json(response)
    listed = {item.get("id") for item in payload.get("data") or [] if isinstance(item, dict)}
    unavailable = {item.get("id"): item for item in payload.get("unavailable") or [] if isinstance(item, dict)}
    absent = [model for model in wanted if model not in listed]
    if not absent:
        return True, f"{' and '.join(wanted)} {'are' if len(wanted) > 1 else 'is'} listed."
    reasons = []
    for model in absent:
        item = unavailable.get(model)
        if item is not None:
            why = item.get("detail") or item.get("reason") or "no reason given"
            reasons.append(f"{model} is listed as unavailable: {str(why)[:200]}")
        else:
            reasons.append(f"{model} is not among the {len(listed)} models the engine lists")
    return False, "; ".join(reasons) + ". Load it on the engine, or set the judge models to ones it serves."


def _result(found: dict[str, tuple[bool, str]], models: list[str]) -> dict:
    checks = []
    for name, label in LABELS.items():
        if name in found:
            ok, detail = found[name]
        else:
            ok, detail = None, "Not checked until the engine answers."
        checks.append({"name": name, "label": label, "ok": ok, "detail": detail})
    failed = [item for item in checks if item["ok"] is False]
    return {
        "ready": not failed,
        "reason": failed[0]["detail"] if failed else "",
        "models": models,
        "checks": checks,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def address(cfg: Settings) -> str:
    """The engine origin without credentials, for the admin to see where the studio looks."""
    try:
        url = httpx.URL(normalize_base_url(cfg.inference_base_url))
    except EvalNotConfigured:
        return ""
    port = f":{url.port}" if url.port else ""
    return f"{url.scheme}://{url.host}{port}"


def last_cycle(db: Session, project_id: str, cfg: Settings) -> dict | None:
    """The study's latest cycle that asked its judges, and whether they were served by one model."""
    rows = db.scalars(
        select(EvalCycle).join(Run, Run.id == EvalCycle.run_id).where(Run.project_id == project_id).order_by(EvalCycle.created_at.desc())
    )
    for cycle in rows:
        agreement = cycle.agreement or {}
        if not agreement.get("served_by"):
            continue
        return {
            "run_id": cycle.run_id,
            "created_at": cycle.created_at.isoformat() if cycle.created_at else None,
            "models": cycle.models or [],
            "served_by": agreement["served_by"],
            "same_model": agreement.get("same_model") or [],
            # When the judge models have changed since, the last cycle's pairing says nothing about the next.
            "same_judges": sorted(cycle.models or []) == sorted(judge_models(cfg)),
        }
    return None
