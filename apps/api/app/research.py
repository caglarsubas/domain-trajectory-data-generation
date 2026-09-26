"""A deep search for a study, as the `deep_search` job runs it.

The account's key is decrypted only here, sent only to its provider, and never written to the job.
"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import runtime
from app.models import CorpusItem, Credential, Project
from app.providers import get_provider
from app.search import DeepSearchError, default_query, run_deep_search
from app.security import decrypt_secret
from app.settings import Settings
from sectors.registry import get_sector
from sectors.steering import scrub_text


def deep_search_for_project(db: Session, project: Project, payload: dict, cfg: Settings, progress=None) -> dict:
    credential = db.get(Credential, payload["credential_id"])
    if credential is None or credential.account_id != project.owner_id:
        raise HTTPException(status_code=404, detail="credential not found")
    if not credential.ready:
        raise HTTPException(status_code=422, detail="credential is not ready for deep search")
    spec = get_provider(credential.provider)
    sector = get_sector(project.sector)
    query = (payload.get("query") or "").strip() or default_query(
        payload["sub_domains"],
        payload["language"],
        label=sector.label.lower(),
        events=list(sector.event_namespace),
    )
    if progress is not None:
        progress(0, 2, f"Searching the web with {spec.label}.")
    secret = decrypt_secret(cfg.credential_master_key, credential.ciphertext)
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
    if progress is not None:
        progress(1, 2, "Storing the report.")
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
