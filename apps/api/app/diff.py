"""What changed between a run and its parent: configuration, notes applied, data, and the judge's scores."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EvalCycle, EvalVerdict, Feedback, Run

# Keys that differ on every child and say nothing about the data.
IGNORED = {"credential_id", "regeneration"}
TOP_EVENT_SHIFTS = 12
TOP_VARIANTS = 8


def _generation(run: Run) -> dict:
    return run.generation or ((run.candidate or {}).get("generation") or {})


def _dig(data: dict, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _latest(db: Session, run: Run) -> tuple[EvalCycle | None, list]:
    cycle = db.scalars(select(EvalCycle).where(EvalCycle.run_id == run.id).order_by(EvalCycle.cycle_index.desc())).first()
    verdicts = list(db.scalars(select(EvalVerdict).where(EvalVerdict.cycle_id == cycle.id))) if cycle else []
    return cycle, verdicts


def _per_hundred(overview: dict) -> dict[str, float]:
    journeys = overview.get("journeys") or 0
    if not journeys:
        return {}
    return {node["type"]: round(100 * node["count"] / journeys, 1) for node in overview.get("nodes") or []}


def diff_runs(db: Session, parent: Run, child: Run) -> dict:
    from app.serialize import headline_score

    before, after = _generation(parent), _generation(child)
    config = [
        {"key": key, "before": parent.config.get(key), "after": child.config.get(key)}
        for key in sorted(set(parent.config or {}) | set(child.config or {}))
        if key not in IGNORED and (parent.config or {}).get(key) != (child.config or {}).get(key)
    ]
    measures = [
        ("Journeys", ("primary_trajectories",)),
        ("Events", ("event_count",)),
        ("Distinct variants", ("overview", "distinct_variants")),
        ("Distinct sequences per 100 journeys", ("quality", "comprehensive", "distinct_per_100")),
        ("Rare path share", ("quality", "comprehensive", "rare_path_share")),
        ("Rule violations", ("quality", "complete", "hard_check_violations")),
        ("Accepted groups", ("rewards", "accepted_groups")),
        ("Pass rate", ("rewards", "pass_rate")),
    ]
    metrics = [{"name": name, "before": _dig(before, *path), "after": _dig(after, *path)} for name, path in measures]
    metrics = [item for item in metrics if item["before"] is not None or item["after"] is not None]

    old, new = _per_hundred(before.get("overview") or {}), _per_hundred(after.get("overview") or {})
    shifts = [
        {"type": name, "before": old.get(name, 0.0), "after": new.get(name, 0.0)}
        for name in set(old) | set(new)
        if old.get(name, 0.0) != new.get(name, 0.0)
    ]
    shifts.sort(key=lambda item: (-abs(item["after"] - item["before"]), item["type"]))

    old_variants = {item["id"]: item for item in _dig(before, "overview", "variants") or []}
    new_variants = {item["id"]: item for item in _dig(after, "overview", "variants") or []}
    appeared = [item for key, item in new_variants.items() if key not in old_variants][:TOP_VARIANTS]
    vanished = [item for key, item in old_variants.items() if key not in new_variants][:TOP_VARIANTS]

    scores = {}
    old_cycle, old_verdicts = _latest(db, parent)
    new_cycle, new_verdicts = _latest(db, child)
    for label, cycle, verdicts in (("before", old_cycle, old_verdicts), ("after", new_cycle, new_verdicts)):
        if cycle is None:
            scores[label] = None
            continue
        primary = (cycle.models or [None])[0]
        scores[label] = {
            "cycle": cycle.cycle_index,
            "accepted": bool(cycle.accepted),
            "headline": headline_score(cycle, verdicts),
            "rubrics": {rubric: by_model.get(primary) for rubric, by_model in (cycle.scores or {}).items()} if primary else {},
        }

    notes = after.get("notes") or {}
    inherited = child.inherited_feedback_ids or []
    comments = {row.id: row for row in db.scalars(select(Feedback).where(Feedback.id.in_(inherited)))} if inherited else {}
    return {
        "parent_run_id": parent.id,
        "regeneration": child.config.get("regeneration"),
        "config": config,
        "notes": {
            "feedback": notes.get("feedback", []),
            "revisions": notes.get("revisions", []),
            "inherited": [
                {"id": key, "stance": comments[key].stance, "comment": comments[key].comment} for key in inherited if key in comments
            ],
        },
        "metrics": metrics,
        "event_shifts": shifts[:TOP_EVENT_SHIFTS],
        "variants": {
            "appeared": [{"id": item["id"], "kind": item.get("kind"), "types": item["types"], "count": item["count"]} for item in appeared],
            "vanished": [{"id": item["id"], "kind": item.get("kind"), "types": item["types"], "count": item["count"]} for item in vanished],
        },
        "scores": scores,
    }
