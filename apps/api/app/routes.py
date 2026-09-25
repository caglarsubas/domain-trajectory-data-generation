from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.evaluation import evaluate_bundle
from app.generation import candidate_for_run
from app.judge import EvalNotConfigured, InferenceEngineClient, JudgeUnavailable
from app.models import Account, CorpusItem, Credential, EvalCycle, EvalVerdict, Feedback, Project, Run
from app.providers import PROVIDERS, get_provider
from app.schemas import (
    CorpusLinkBody,
    CredentialBody,
    CredentialUpdateBody,
    DeepSearchBody,
    EvaluateBody,
    FeedbackBody,
    LoginBody,
    ProjectBody,
    RegisterBody,
    RerunBody,
    RunBody,
)
from app.search import DeepSearchError, default_query, run_deep_search
from app import runtime
from app.security import decrypt_secret, encrypt_secret, fingerprint, hash_password, issue_token, read_token, verify_password
from sectors.banking.corpus import scrub_text
from sectors.registry import get_sector
from app.serialize import project_out, run_out
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
            }
        )
    return {"data": data}


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
    row = Credential(
        account_id=account.id,
        provider=spec.id,
        label=body.label,
        ciphertext=encrypt_secret(cfg.credential_master_key, body.secret),
        fingerprint=fingerprint(body.secret),
        scope=scope,
        ready=1,
    )
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
        row.ciphertext = encrypt_secret(cfg.credential_master_key, body.secret)
        row.fingerprint = fingerprint(body.secret)
    db.commit()
    db.refresh(row)
    return _credential_out(row)


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
    path = cfg.upload_dir / f"{digest}-{upload.filename or 'document'}"
    path.write_bytes(payload)
    item = CorpusItem(
        project_id=project.id,
        kind=kind,
        name=upload.filename or "document",
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
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "uri": item.uri,
        "content_hash": item.content_hash,
        "provenance": item.provenance,
    }


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
        spec = get_provider(credential.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    secret = decrypt_secret(cfg.credential_master_key, credential.ciphertext)
    query = (body.query or "").strip() or default_query(
        body.sub_domains,
        body.language,
        label=sector.label.lower(),
        events=list(sector.event_namespace),
    )
    try:
        if runtime.searcher is not None:
            result = runtime.searcher.search(query, provider=spec.id, key=secret)
        else:
            result = run_deep_search(spec.id, query, key=secret)
    except DeepSearchError as exc:
        raise HTTPException(status_code=502, detail=f"{spec.label} deep search failed") from exc
    text = scrub_text(result.text).strip()
    if secret and secret in text:
        text = text.replace(secret, "")
    if not text:
        raise HTTPException(status_code=502, detail=f"{spec.label} deep search returned no text")
    sources = [item for item in result.sources if item.startswith(("https://", "http://"))][:12]
    stored = text[:12000]
    if sources:
        stored = f"{stored}\n\nSources:\n" + "\n".join(sources)
    digest = hashlib.sha256(stored.encode()).hexdigest()
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.upload_dir / f"{digest}-deep-search-{spec.id}.txt"
    path.write_text(stored, encoding="utf-8")
    item = CorpusItem(
        project_id=project.id,
        kind="deep_search",
        name=f"Deep search · {spec.label}",
        storage_path=str(path),
        content_hash=digest,
        provenance=f"provider:{spec.id}",
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
        "provider": spec.id,
        "model": result.model,
        "sources": sources,
        "excerpt": text[:600],
    }


@router.post("/runs")
def create_run(body: RunBody, account: AccountDep, db: Db) -> dict:
    config = config_from_body(body, account, db)
    bundle = candidate_for_run(db, config, project_id=body.project_id, feedback_rows=[], parent=None)
    run = Run(
        project_id=body.project_id,
        owner_id=account.id,
        status="generated",
        config=config,
        inherited_feedback_ids=[],
        candidate=bundle.model_dump(mode="json"),
        cycle_count=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run_out(run, db)


@router.get("/runs")
def list_runs(account: AccountDep, db: Db) -> dict:
    rows = list(db.scalars(select(Run).where(Run.owner_id == account.id).order_by(Run.created_at)))
    return {"data": [run_out(row, db) for row in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, account: AccountDep, db: Db) -> dict:
    return run_out(require_run(db, run_id, account), db)


@router.post("/runs/{run_id}/feedback")
def add_feedback(run_id: str, body: FeedbackBody, account: AccountDep, db: Db) -> dict:
    run = require_run(db, run_id, account)
    bundle = TrajectoryBundle.model_validate(run.candidate) if run.candidate else banking_fixture()
    if body.target_type == "run" and body.target_id != run.id:
        raise HTTPException(status_code=422, detail="run feedback must target this run")
    if body.target_type == "trajectory" and body.target_id not in {item.trajectory_id for item in bundle.trajectories}:
        raise HTTPException(status_code=422, detail="unknown trajectory")
    if body.target_type == "event" and body.target_id not in {item.event_id for item in bundle.events}:
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
def rerun(run_id: str, body: RerunBody, account: AccountDep, db: Db) -> dict:
    parent = require_run(db, run_id, account)
    config, feedback_ids = rerun_config(parent, body, account, db)
    rows = []
    if feedback_ids:
        rows = list(db.scalars(select(Feedback).where(Feedback.id.in_(feedback_ids))))
        order = {item: index for index, item in enumerate(feedback_ids)}
        rows.sort(key=lambda row: order.get(row.id, 0))
    bundle = candidate_for_run(db, config, project_id=parent.project_id, feedback_rows=rows, parent=parent)
    child = Run(
        project_id=parent.project_id,
        owner_id=account.id,
        parent_run_id=parent.id,
        status="generated",
        config=config,
        inherited_feedback_ids=feedback_ids,
        candidate=bundle.model_dump(mode="json"),
        cycle_count=0,
    )
    db.add(child)
    db.commit()
    db.refresh(child)
    return run_out(child, db)


def _judge_for(cfg: Settings) -> InferenceEngineClient:
    try:
        return InferenceEngineClient(
            base_url=cfg.inference_base_url,
            api_key=cfg.inference_api_key,
            tenant=cfg.inference_tenant,
            org_id=cfg.inference_org_id,
            key_id=cfg.inference_key_id,
            judge_model=cfg.inference_judge_model,
        )
    except EvalNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/runs/{run_id}/evaluate")
def evaluate(run_id: str, body: EvaluateBody, account: AccountDep, db: Db, cfg: Cfg) -> dict:
    run = require_run(db, run_id, account)
    if run.cycle_count >= int(run.config["max_cycles"]):
        raise HTTPException(status_code=409, detail="max evaluation cycles reached")
    if body.candidate is not None:
        bundle = TrajectoryBundle.model_validate(body.candidate)
        run.candidate = body.candidate
    elif run.candidate:
        bundle = TrajectoryBundle.model_validate(run.candidate)
    else:
        bundle = banking_fixture()
    items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == run.project_id)))
    created: list[InferenceEngineClient] = []
    cached: dict[str, InferenceEngineClient] = {}

    def resolve_judge():
        if runtime.judge is not None:
            return runtime.judge
        if "client" not in cached:
            client = _judge_for(cfg)
            cached["client"] = client
            created.append(client)
        return cached["client"]

    class _Lazy:
        def run_eval(self, **kwargs):
            return resolve_judge().run_eval(**kwargs)

    try:
        result = evaluate_bundle(
            bundle=bundle,
            sector_id=run.config["sector"],
            sub_domains=run.config["sub_domains"],
            language=run.config["language"],
            cold_start=run.config["start_mode"] == "cold",
            corpus_items=items,
            thresholds=run.config["thresholds"],
            judge=_Lazy(),
        )
    except JudgeUnavailable as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc
    finally:
        for client in created:
            client.close()
    cycle = EvalCycle(
        run_id=run.id,
        cycle_index=run.cycle_count + 1,
        hard_check_passed=1 if result["hard_check_passed"] else 0,
        hard_check_errors=result["hard_check_errors"],
        reference_quality=result["reference_quality"],
        accepted=1 if result["accepted"] else 0,
        revision_notes=result["revision_notes"],
        judge_tenant=cfg.inference_tenant if result["called_judge"] else "",
        judge_org_id=cfg.inference_org_id if result["called_judge"] else "",
        judge_key_id=cfg.inference_key_id if result["called_judge"] else "",
    )
    db.add(cycle)
    db.flush()
    for verdict in result["verdicts"]:
        db.add(
            EvalVerdict(
                cycle_id=cycle.id,
                rubric=verdict["rubric"],
                score=verdict["score"],
                parsed=verdict["parsed"],
                raw=verdict["raw"],
                judge_model=verdict["judge_model"],
                duration_ms=verdict["duration_ms"],
            )
        )
    run.cycle_count += 1
    run.status = "evaluated"
    db.commit()
    return run_out(run, db)
