"""Build a sector candidate when a run is created or repeated."""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.corpus_text import ordered, read_corpus_text, read_document
from app.models import CorpusItem, EvalCycle, Run
from sectors.registry import get_sector


def candidate_for_run(
    db: Session,
    config: dict,
    *,
    project_id: str,
    feedback_rows: list,
    parent: Run | None,
    progress=None,
):
    items = []
    corpus_text = ""
    if config.get("start_mode") == "warm":
        items = ordered(list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == project_id))))
        corpus_text = read_corpus_text(items)
    revisions: list[str] = []
    if parent is not None:
        cycles = list(db.scalars(select(EvalCycle).where(EvalCycle.run_id == parent.id)))
        for cycle in cycles:
            revisions.extend(cycle.revision_notes or [])
    feedback = [
        {
            "target_type": row.target_type,
            "target_id": row.target_id,
            "stance": row.stance,
            "comment": row.comment,
        }
        for row in feedback_rows
    ]
    seed_payload = {
        "config": {key: config[key] for key in sorted(config) if key != "credential_id"},
        "corpus_text": corpus_text,
        "feedback": feedback,
        "revisions": revisions,
        "corpus": sorted(item.content_hash for item in items),
    }
    sector = get_sector(config["sector"])
    bundle = sector.generate(
        sub_domains=list(config["sub_domains"]),
        language=config["language"],
        target_trajectory_count=int(config["target_trajectory_count"]),
        event_budget=config.get("event_budget"),
        min_events=int(config["min_events"]),
        max_events=int(config["max_events"]),
        max_assistant_turns=int(config["max_assistant_turns"]),
        start_mode=config["start_mode"],
        reward_mechanism=config["reward_mechanism"],
        signal_mechanism=config["signal_mechanism"],
        consumer=config["consumer"],
        target_family=config["target_family"],
        corpus_text=corpus_text,
        feedback=feedback,
        revision_notes=revisions,
        parent_bundle=parent.candidate if parent is not None else None,
        seed=json.dumps(seed_payload, sort_keys=True, default=str),
        group_size=int(config.get("group_size") or 1),
        progress=progress,
    )
    errors = sector.hard_checks(bundle)
    if errors:
        raise HTTPException(status_code=500, detail=f"generated trajectory failed {sector.id} checks")
    if bundle.generation is not None and bundle.generation.steering is not None:
        bundle.generation.steering["documents"] = [_document_report(sector, item) for item in items]
    return bundle


def _document_report(sector, item) -> dict:
    doc = read_document(item)
    found = sector.steering(doc.text) if doc.readable else None
    return {
        "id": doc.item_id,
        "kind": doc.kind,
        "name": doc.name,
        "readable": doc.readable,
        "reason": doc.reason,
        "characters": len(doc.text),
        "terms": list(found.terms) if found else [],
        "named_events": list(found.events) if found else [],
        "negated_events": list(found.negated) if found else [],
    }
