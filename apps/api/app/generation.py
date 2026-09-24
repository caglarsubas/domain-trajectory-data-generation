"""Build a banking candidate when a run is created or repeated."""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.corpus_text import read_corpus_excerpt
from app.models import CorpusItem, EvalCycle, Run
from sectors.banking.checks import banking_hard_checks
from sectors.banking.generate import generate_banking_bundle


def candidate_for_run(
    db: Session,
    config: dict,
    *,
    project_id: str,
    feedback_rows: list,
    parent: Run | None,
):
    items = []
    excerpt = ""
    if config.get("start_mode") == "warm":
        items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == project_id)))
        excerpt = read_corpus_excerpt(items)
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
        "excerpt": excerpt,
        "feedback": feedback,
        "revisions": revisions,
        "corpus": sorted(item.content_hash for item in items),
    }
    bundle = generate_banking_bundle(
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
        corpus_text=excerpt,
        feedback=feedback,
        revision_notes=revisions,
        parent_bundle=parent.candidate if parent is not None else None,
        seed=json.dumps(seed_payload, sort_keys=True, default=str),
    )
    errors = banking_hard_checks(bundle)
    if errors:
        raise HTTPException(status_code=500, detail="generated trajectory failed banking checks")
    return bundle
