from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from datetime import datetime, timezone

from app.db import get_db
from app.generation import ACCEPTANCE_CEILING
from app.models import Account, CorpusItem, Credential, EvalCycle, Feedback, Job, Project, Run
from app.providers import PROVIDERS, KeyCheck, check_key, get_provider
from app import quotas
from app.schemas import (
    CorpusLinkBody,
    CredentialBody,
    CredentialUpdateBody,
    DeepSearchBody,
    EvaluateBody,
    ExportBody,
    FeedbackBody,
    LoginBody,
    ProjectBody,
    RegenerateBody,
    RegisterBody,
    RerunBody,
    RunBody,
)
from app import runtime
from app.security import decrypt_secret, encrypt_secret, fingerprint, hash_password, issue_token, read_token, verify_password
from sectors.journeys import STUDIO_TRAJECTORY_CAP
from sectors.registry import get_sector
from app import export as run_export
from app import jobs
from app.ingest import safe_name
from app.serialize import corpus_out, job_out, project_out, run_out, run_summary
from app.store import MAX_RUN_SEQUENCES, SMALL_RUN_SEQUENCES, DbStore, run_dir, store_for
from app.service import config_from_body, require_project, require_run, rerun_config
from app.settings import Settings, load_settings
from trajectory_contract import TrajectoryBundle, banking_fixture

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def settings() -> Settings:
    return load_settings()


def db_session() -> Session:
    yield from get_db()


Db = Annotated[Session, Depends(db_session)]
Cfg = Annotated[Settings, Depends(settings)]


def current_account(
    db: Db,
    cfg: Cfg,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Account:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing token")
    payload = read_token(cfg.jwt_secret, credentials.credentials)
    account = db.get(Account, payload["sub"])
    if account is None:
        raise HTTPException(status_code=401, detail="unknown account")
    return account


AccountDep = Annotated[Account, Depends(current_account)]


def _public_account(account: Account) -> dict:
    return {"id": account.id, "email": account.email, "kind": account.kind}


@router.post("/auth/register")
def register(body: RegisterBody, db: Db, cfg: Cfg) -> dict:
    existing = db.scalar(select(Account).where(Account.email == body.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    account = Account(email=body.email, password_hash=hash_password(body.password), kind=body.kind)
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"access_token": issue_token(cfg.jwt_secret, account.id, account.kind), "account": _public_account(account)}


@router.post("/auth/login")
def login(body: LoginBody, db: Db, cfg: Cfg) -> dict:
    account = db.scalar(select(Account).where(Account.email == body.email))
    if account is None or not verify_password(body.password, account.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return {"access_token": issue_token(cfg.jwt_secret, account.id, account.kind), "account": _public_account(account)}


@router.get("/auth/me")
def me(account: AccountDep) -> dict:
    return _public_account(account)


@router.get("/providers")
def providers() -> dict:
    return {
        "data": [
            {"id": spec.id, "label": spec.label, "supports_deep_search": spec.supports_deep_search}
            for spec in PROVIDERS.values()
        ]
    }


@router.get("/sectors")
def sectors() -> dict:
    from sectors.registry import known_sectors, get_sector

    data = []
    for sector_id in known_sectors():
        pack = get_sector(sector_id)
        data.append(
            {
                "id": pack.id,
                "label": pack.label,
                "sub_domains": list(pack.sub_domains),
                "event_namespace": list(pack.event_namespace),
                "state_dimensions": list(pack.state_dimensions),
                "languages": list(pack.languages),
                "studio_cap": STUDIO_TRAJECTORY_CAP,
                "small_run_sequences": SMALL_RUN_SEQUENCES,
                "max_run_sequences": MAX_RUN_SEQUENCES,
                "event_kinds": {name: pack.lifecycle.kind_of(name) for name in pack.event_namespace},
                "lanes": _lanes(pack.lifecycle),
            }
        )
    return {"data": data}


def _lanes(lifecycle) -> list[dict]:
    kinds: list[str] = []
    for name in lifecycle.namespace:
        kind = lifecycle.kind_of(name)
        if kind not in kinds:
            kinds.append(kind)
    return [{"kind": kind, "object_type": lifecycle.object_types.get(kind, kind)} for kind in kinds]


@router.get("/fixture/banking")
def fixture() -> dict:
    return {
        "bundle_source": "fixture",
        "generation_active": False,
        "note": "Sample banking journey. Generation is not running.",
        "bundle": banking_fixture().model_dump(mode="json"),
    }


@router.post("/credentials")
def create_credential(body: CredentialBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    if body.scope == "platform" and account.kind != "admin":
        raise HTTPException(status_code=403, detail="only admin can store platform credentials")
    scope = body.scope if account.kind == "admin" else "byok"
    try:
        spec = get_provider(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    problem = spec.validate(body.secret)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    if not spec.supports_deep_search:
        raise HTTPException(status_code=422, detail="provider cannot run deep search")
    check = _check_key(spec.id, body.secret)
    if check.status == "rejected":
        raise HTTPException(status_code=422, detail=check.detail)
    row = Credential(
        account_id=account.id,
        provider=spec.id,
        label=body.label,
        ciphertext=encrypt_secret(cfg.credential_master_key, body.secret),
        fingerprint=fingerprint(body.secret),
        scope=scope,
    )
    _record_check(row, check)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _credential_out(row)


@router.get("/credentials")
def list_credentials(account: AccountDep, db: Db) -> dict:
    rows = list(db.scalars(select(Credential).where(Credential.account_id == account.id)))
    return {"data": [_credential_out(row) for row in rows]}


def _own_credential(db: Session, credential_id: str, account: Account) -> Credential:
    row = db.get(Credential, credential_id)
    if row is None or row.account_id != account.id:
        raise HTTPException(status_code=404, detail="credential not found")
    return row


@router.patch("/credentials/{credential_id}")
def update_credential(credential_id: str, body: CredentialUpdateBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    row = _own_credential(db, credential_id, account)
    if body.label is not None:
        row.label = body.label
    if body.secret is not None:
        problem = get_provider(row.provider).validate(body.secret)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        check = _check_key(row.provider, body.secret)
        if check.status == "rejected":
            # The old key stays in place.
            raise HTTPException(status_code=422, detail=check.detail)
        row.ciphertext = encrypt_secret(cfg.credential_master_key, body.secret)
        row.fingerprint = fingerprint(body.secret)
        _record_check(row, check)
    db.commit()
    db.refresh(row)
    return _credential_out(row)


@router.post("/credentials/{credential_id}/check")
def check_credential(credential_id: str, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    row = _own_credential(db, credential_id, account)
    _record_check(row, _check_key(row.provider, decrypt_secret(cfg.credential_master_key, row.ciphertext)))
    db.commit()
    db.refresh(row)
    return _credential_out(row)


def _check_key(provider: str, secret: str) -> KeyCheck:
    if runtime.key_checker is not None:
        return runtime.key_checker.check(provider, secret)
    return check_key(provider, secret)


def _record_check(row: Credential, check: KeyCheck) -> None:
    """Only a key the provider accepted is ready; an unreachable provider leaves it saved but not ready."""
    row.ready = 1 if check.status == "valid" else 0
    row.check_status = check.status
    row.check_detail = check.detail
    row.checked_at = datetime.now(timezone.utc)


@router.delete("/credentials/{credential_id}", status_code=204)
def delete_credential(credential_id: str, account: AccountDep, db: Db) -> None:
    row = _own_credential(db, credential_id, account)
    db.delete(row)
    db.commit()


def _credential_out(row: Credential) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "label": row.label,
        "fingerprint": row.fingerprint,
        "scope": row.scope,
        "ready": bool(row.ready),
        "supports_deep_search": True,
        "check_status": row.check_status,
        "check_detail": row.check_detail,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
    }


@router.post("/projects")
def create_project(body: ProjectBody, account: AccountDep, db: Db) -> dict:
    try:
        sector = get_sector(body.sector)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project = Project(owner_id=account.id, name=body.name, sector=sector.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project_out(project, db)


@router.get("/projects")
def list_projects(account: AccountDep, db: Db) -> dict:
    rows = list(db.scalars(select(Project).where(Project.owner_id == account.id)))
    return {"data": [project_out(row, db) for row in rows]}


@router.post("/projects/{project_id}/corpus")
async def upload_corpus(
    project_id: str,
    account: AccountDep,
    db: Db,
    cfg: Cfg,
    kind: Annotated[str, Form()],
    upload: Annotated[UploadFile, File()],
) -> dict:
    project = require_project(db, project_id, account)
    if kind not in {"deep_search", "paper", "repo", "data_source", "ontology", "other"}:
        raise HTTPException(status_code=422, detail="unknown corpus kind")
    payload = await upload.read()
    if not payload:
        raise HTTPException(status_code=422, detail="empty file")
    if len(payload) > 20_000_000:
        raise HTTPException(status_code=422, detail="file exceeds 20MB")
    digest = hashlib.sha256(payload).hexdigest()
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    # The client's file name never becomes a path: a name like ../../x stays inside the upload directory.
    path = cfg.upload_dir / f"{digest}-{safe_name(upload.filename)}"
    path.write_bytes(payload)
    item = CorpusItem(
        project_id=project.id,
        kind=kind,
        name=(upload.filename or "document")[:300],
        storage_path=str(path),
        content_hash=digest,
        provenance="upload",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "content_hash": item.content_hash,
        "provenance": item.provenance,
    }


@router.post("/projects/{project_id}/corpus/link")
def link_corpus(project_id: str, body: CorpusLinkBody, account: AccountDep, db: Db) -> dict:
    project = require_project(db, project_id, account)
    digest = hashlib.sha256(body.uri.encode()).hexdigest()
    item = CorpusItem(
        project_id=project.id,
        kind=body.kind,
        name=body.name,
        uri=body.uri,
        content_hash=digest,
        provenance="link",
        ingest={"status": "pending"},
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    # The link is fetched by a job; a link that cannot be fetched is kept and says why.
    job = jobs.enqueue(db, kind="fetch", owner_id=account.id, run_id=None, project_id=project.id, payload={"item_id": item.id})
    db.refresh(item)
    return {**corpus_out(item), "job": job_out(job)}


@router.post("/projects/{project_id}/deep-search")
def deep_search_corpus(project_id: str, body: DeepSearchBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    project = require_project(db, project_id, account)
    try:
        sector = get_sector(project.sector)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    unknown = [name for name in body.sub_domains if name not in sector.sub_domains]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown sub-domains: {', '.join(unknown)}")
    credential = db.get(Credential, body.credential_id)
    if credential is None or credential.account_id != account.id:
        raise HTTPException(status_code=404, detail="credential not found")
    if credential.scope == "platform" and account.kind != "admin":
        raise HTTPException(status_code=403, detail="platform credentials are only available to admin")
    if account.kind in {"user", "demo"} and credential.scope != "byok":
        raise HTTPException(status_code=403, detail="user and demo runs require your own key")
    if not credential.ready:
        raise HTTPException(status_code=422, detail="credential is not ready for deep search")
    try:
        get_provider(credential.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    quotas.require_daily(db, account, cfg, "deep_searches")
    payload = {"credential_id": credential.id, "sub_domains": body.sub_domains, "language": body.language, "query": body.query}
    job = jobs.enqueue(db, kind="deep_search", owner_id=account.id, run_id=None, project_id=project.id, payload=payload)
    _raise_if_refused(db, job)
    # Inline, the job has finished and its corpus item is here; otherwise poll GET /jobs/{id}.
    return {**(job.result or {}), "job": job_out(job)}


def _raise_if_refused(db: Session, job: Job) -> None:
    """A job run inline that refused answers as the request did before jobs, with the same status and detail."""
    db.refresh(job)
    status = (job.result or {}).get("http_status")
    if job.status == "failed" and status:
        raise HTTPException(status_code=status, detail=job.error)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, account: AccountDep, db: Db) -> dict:
    return job_out(_own_job(db, job_id, account))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, account: AccountDep, db: Db) -> dict:
    job = _own_job(db, job_id, account)
    if job.status not in jobs.ACTIVE:
        raise HTTPException(status_code=409, detail="this job is not running")
    jobs.cancel(db, job)
    db.refresh(job)
    return job_out(job)


def _own_job(db: Session, job_id: str, account: Account) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.owner_id != account.id:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/quota")
def quota(account: AccountDep, db: Db, cfg: Cfg) -> dict:
    """A demo account's daily limits and use. Other accounts have no limits."""
    return {"kind": account.kind, "demo": quotas.usage(db, account, cfg)}


@router.post("/runs")
def create_run(body: RunBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    config = config_from_body(body, account, db)
    _require_run_quota(db, account, cfg, config)
    run = Run(
        project_id=body.project_id,
        owner_id=account.id,
        status="queued",
        config=config,
        inherited_feedback_ids=[],
        candidate=None,
        cycle_count=0,
    )
    db.add(run)
    db.commit()
    jobs.enqueue(db, kind="generate", owner_id=account.id, run_id=run.id, payload={"feedback_ids": []})
    db.refresh(run)
    return run_out(run, db)


@router.get("/runs")
def list_runs(account: AccountDep, db: Db) -> dict:
    rows = list(
        db.scalars(select(Run).options(defer(Run.candidate)).where(Run.owner_id == account.id).order_by(Run.created_at))
    )
    return {"data": [run_summary(row, db) for row in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, account: AccountDep, db: Db) -> dict:
    return run_out(require_run(db, run_id, account), db)


@router.get("/runs/{run_id}/export/{part}")
def export_run(
    run_id: str, part: str, account: AccountDep, db: Db, held_out: str | None = None, allow_unaccepted: bool = False
) -> Response:
    run = require_run(db, run_id, account)
    if part not in run_export.PARTS:
        raise HTTPException(status_code=404, detail=f"unknown export part; choose one of {', '.join(run_export.PARTS)}")
    sector = get_sector(run.config.get("sector", "banking"))
    if held_out is not None and held_out not in (run.config.get("sub_domains") or []):
        raise HTTPException(status_code=422, detail="the held-out sub-domain must be one of this run's sub-domains")
    unaccepted = _require_acceptance(run, db, allow_unaccepted)
    if not run.candidate:
        if store_for(run) is None:
            raise HTTPException(status_code=409, detail="this run has no generated candidate to export")
        path = run_export.part_path(run_dir(run.id), held_out, part, unaccepted)
        if not run_export.prepared(run_dir(run.id), held_out, unaccepted) or not path.is_file():
            raise HTTPException(status_code=409, detail="prepare this export first; large runs are exported by a background job")
        suffix = f"-heldout-{held_out}" if held_out else ""
        return FileResponse(
            path,
            media_type="application/json" if part == "manifest.json" else "application/gzip",
            filename=f"run-{run.id[:8]}{suffix}-{path.name}",
        )
    bundle = TrajectoryBundle.model_validate(run.candidate)
    parts = run_export.build(run, bundle, sector, run_out(run, db)["cycles"], held_out)
    media = "application/x-ndjson" if part.endswith(".jsonl") else "application/json"
    suffix = f"-heldout-{held_out}" if held_out else ""
    return Response(
        content=parts[part],
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="run-{run.id[:8]}{suffix}-{part}"'},
    )


@router.post("/runs/{run_id}/exports")
def prepare_export(run_id: str, body: ExportBody, account: AccountDep, db: Db) -> dict:
    run = require_run(db, run_id, account)
    _require_generated(run)
    if run.candidate:
        raise HTTPException(status_code=409, detail="small runs export directly; download the parts")
    if store_for(run) is None:
        raise HTTPException(status_code=409, detail="this run has nothing to export")
    if body.held_out is not None and body.held_out not in (run.config.get("sub_domains") or []):
        raise HTTPException(status_code=422, detail="the held-out sub-domain must be one of this run's sub-domains")
    unaccepted = _require_acceptance(run, db, body.allow_unaccepted)
    current = next(
        (item for item in list_exports(run_id, account, db)["data"] if item["held_out"] == body.held_out and item["unaccepted"] == unaccepted),
        None,
    )
    if current is None or not (current["ready"] or current["job"]["status"] in {"queued", "running"}):
        jobs.enqueue(db, kind="export", owner_id=account.id, run_id=run.id, payload={"held_out": body.held_out, "unaccepted": unaccepted})
    return list_exports(run_id, account, db)


def _latest_cycle(run: Run, db: Session) -> EvalCycle | None:
    return db.scalars(select(EvalCycle).where(EvalCycle.run_id == run.id).order_by(EvalCycle.cycle_index.desc())).first()


def _require_acceptance(run: Run, db: Session, allow_unaccepted: bool) -> bool:
    """Export follows the judge: an accepted run exports as it is, any other only on request. True when unaccepted."""
    cycle = _latest_cycle(run, db)
    if cycle is not None and cycle.accepted:
        return False
    if not allow_unaccepted:
        state = "has not judged this run yet" if cycle is None else "did not accept this run"
        raise HTTPException(
            status_code=409,
            detail=f"The judge {state}. Export it anyway with allow_unaccepted=true; the manifest will say it was not accepted.",
        )
    return True


@router.get("/runs/{run_id}/exports")
def list_exports(run_id: str, account: AccountDep, db: Db) -> dict:
    run = require_run(db, run_id, account)
    root = run_dir(run.id)
    rows = list(db.scalars(select(Job).where(Job.run_id == run.id, Job.kind == "export").order_by(Job.created_at)))
    latest: dict[tuple, Job] = {}
    for row in rows:
        latest[(row.payload.get("held_out"), bool(row.payload.get("unaccepted")))] = row
    data = []
    for (held_out, unaccepted), job in latest.items():
        manifest = run_export.part_path(root, held_out, "manifest.json", unaccepted)
        ready = manifest.is_file()
        sizes = json.loads(manifest.read_text()).get("file_sizes", {}) if ready else {}
        downloads = {part: run_export.part_path(root, held_out, part, unaccepted).stat().st_size for part in run_export.PARTS} if ready else {}
        data.append(
            {"held_out": held_out, "unaccepted": unaccepted, "ready": ready, "sizes": sizes, "download_sizes": downloads, "job": job_out(job)}
        )
    return {"data": data}


@router.get("/runs/{run_id}/journeys")
def list_journeys(run_id: str, account: AccountDep, db: Db, offset: int = 0, limit: int = 50, variant: str | None = None) -> dict:
    run = require_run(db, run_id, account)
    _require_generated(run)
    found = store_for(run)
    entries = found.entries() if found else []
    if variant:
        entries = [entry for entry in entries if entry["variant"] == variant]
    limit = max(1, min(limit, 500))
    return {"total": len(entries), "offset": offset, "data": entries[offset : offset + limit], "paged": bool(found and found.paged)}


@router.get("/runs/{run_id}/journeys/{trajectory_id}")
def get_journey(run_id: str, trajectory_id: str, account: AccountDep, db: Db) -> dict:
    run = require_run(db, run_id, account)
    _require_generated(run)
    found = store_for(run)
    journey = found.journey(trajectory_id) if found else None
    if journey is None:
        raise HTTPException(status_code=404, detail="journey not found")
    return journey


@router.post("/runs/{run_id}/feedback")
def add_feedback(run_id: str, body: FeedbackBody, account: AccountDep, db: Db) -> dict:
    run = require_run(db, run_id, account)
    _require_generated(run)
    found = store_for(run) or DbStore(banking_fixture().model_dump(mode="json"))
    if body.target_type == "run" and body.target_id != run.id:
        raise HTTPException(status_code=422, detail="run feedback must target this run")
    if body.target_type == "trajectory" and found.trajectory_type(body.target_id) is None:
        raise HTTPException(status_code=422, detail="unknown trajectory")
    if body.target_type == "event" and found.event_type(body.target_id) is None:
        raise HTTPException(status_code=422, detail="unknown event")
    row = Feedback(
        run_id=run.id,
        author_id=account.id,
        target_type=body.target_type,
        target_id=body.target_id,
        stance=body.stance,
        comment=body.comment,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "stance": row.stance,
        "comment": row.comment,
    }


@router.post("/runs/{run_id}/rerun")
def rerun(run_id: str, body: RerunBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    parent = require_run(db, run_id, account)
    _require_generated(parent)
    config, feedback_ids = rerun_config(parent, body, account, db)
    _require_run_quota(db, account, cfg, config)
    child = Run(
        project_id=parent.project_id,
        owner_id=account.id,
        parent_run_id=parent.id,
        status="queued",
        config=config,
        inherited_feedback_ids=feedback_ids,
        candidate=None,
        cycle_count=0,
    )
    db.add(child)
    db.commit()
    jobs.enqueue(db, kind="generate", owner_id=account.id, run_id=child.id, payload={"feedback_ids": feedback_ids})
    db.refresh(child)
    return run_out(child, db)


@router.post("/runs/{run_id}/regenerate")
def regenerate(run_id: str, body: RegenerateBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    """A child run generated from the parent's revision notes and notes, judged as soon as it is generated."""
    parent = require_run(db, run_id, account)
    _require_generated(parent)
    cycle = _latest_cycle(parent, db)
    if cycle is None:
        raise HTTPException(status_code=409, detail="Ask the judge first; its revision notes steer the regenerated run.")
    if cycle.accepted:
        raise HTTPException(status_code=409, detail="The judge accepted this run; there is nothing to regenerate.")
    current = (parent.config.get("regeneration") or {}).get("round", 1)
    limit = int(parent.config["max_cycles"])
    if current >= limit:
        raise HTTPException(
            status_code=409,
            detail=f"This study has been judged {current} times, its limit. Change the configuration and run it again.",
        )
    known = [row.id for row in db.scalars(select(Feedback).where(Feedback.run_id == parent.id).order_by(Feedback.created_at))]
    feedback_ids = known if body.feedback_ids is None else list(body.feedback_ids)
    if any(item not in known for item in feedback_ids):
        raise HTTPException(status_code=422, detail="feedback does not belong to the parent run")
    config = {**parent.config, "regeneration": {"from_run": parent.id, "round": current + 1, "revision_notes": list(cycle.revision_notes or [])}}
    _require_run_quota(db, account, cfg, config)
    quotas.require_daily(db, account, cfg, "judge_cycles")
    child = Run(
        project_id=parent.project_id,
        owner_id=account.id,
        parent_run_id=parent.id,
        status="queued",
        config=config,
        inherited_feedback_ids=feedback_ids,
        candidate=None,
        cycle_count=0,
    )
    db.add(child)
    db.commit()
    jobs.enqueue(db, kind="generate", owner_id=account.id, run_id=child.id, payload={"feedback_ids": feedback_ids, "judge_after": True})
    db.refresh(child)
    return run_out(child, db)


@router.get("/runs/{run_id}/diff")
def run_diff(run_id: str, account: AccountDep, db: Db) -> dict:
    """What changed from the parent run: configuration, notes and their effects, data, and scores."""
    from app.diff import diff_runs

    run = require_run(db, run_id, account)
    if not run.parent_run_id:
        raise HTTPException(status_code=404, detail="this run has no previous run to compare with")
    return diff_runs(db, require_run(db, run.parent_run_id, account), run)


def judged(cycle: EvalCycle | None) -> bool:
    """A cycle that reached a decision: the hard checks failed, or the primary judge read every rubric."""
    if cycle is None:
        return False
    if not cycle.hard_check_passed:
        return True
    if cycle.models:
        return not any(flag.get("kind") == "unreadable" and flag.get("model") == cycle.models[0] for flag in cycle.flags or [])
    return True


def _require_run_quota(db: Session, account: Account, cfg: Settings, config: dict) -> None:
    sequences = int(config["target_trajectory_count"]) * int(config.get("group_size") or 1)
    if config.get("target_kind") == "accepted_groups":
        # An accepted-group target may draw up to the acceptance ceiling.
        sequences *= ACCEPTANCE_CEILING
    quotas.require_run_size(account, cfg, sequences)
    quotas.require_daily(db, account, cfg, "runs")


def _require_generated(run: Run) -> None:
    if run.status in {"queued", "generating"}:
        raise HTTPException(status_code=409, detail="this run is still generating")
    if run.status in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail=f"this run {run.status}; run it again to get journeys")


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, account: AccountDep, db: Db) -> dict:
    run = require_run(db, run_id, account)
    job = jobs.latest_for(db, run.id)
    if job is None or job.status not in jobs.ACTIVE:
        raise HTTPException(status_code=409, detail="nothing is running for this run")
    jobs.cancel(db, job)
    db.refresh(run)
    return run_out(run, db)


@router.post("/runs/{run_id}/evaluate")
def evaluate(run_id: str, body: EvaluateBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    run = require_run(db, run_id, account)
    _require_generated(run)
    if run.cycle_count >= int(run.config["max_cycles"]):
        raise HTTPException(status_code=409, detail="max evaluation cycles reached")
    current = jobs.latest_for(db, run.id, "evaluate")
    if current is not None and current.status in jobs.ACTIVE:
        raise HTTPException(status_code=409, detail="the judge is already working on this run")
    if body.candidate is None and judged(_latest_cycle(run, db)):
        raise HTTPException(
            status_code=409,
            detail="The judge has already read this run. Regenerate from its notes instead of judging the same journeys again.",
        )
    if body.candidate is not None:
        TrajectoryBundle.model_validate(body.candidate)
        run.candidate = body.candidate
        db.commit()
    quotas.require_daily(db, account, cfg, "judge_cycles")
    job = jobs.enqueue(db, kind="evaluate", owner_id=account.id, run_id=run.id, payload={})
    _raise_if_refused(db, job)
    db.refresh(run)
    return run_out(run, db)
