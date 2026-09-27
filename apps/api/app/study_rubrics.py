"""Study rubrics (4B): the judge proposes a solution and a behavior rubric for a study, the owner reviews them.

The judge reads one group of the run's journeys, a primary and its rollouts with mixed outcomes when the run has
groups, with the study brief and its warm-start passages, and writes each rubric as a title, a description, a few
checkable criteria, and what a journey scoring 5, 3, and 1 shows. The owner edits and approves them. An approved
rubric becomes a declarative engine rubric whose name carries a hash of its content, so an edit is a new rubric; each
cycle of the study registers its approved rubrics and asks them of every sampled journey.

Study rubrics are reported, next to the code's solution and behavior rubrics they are compared with, and never decide
acceptance: a stratified sample holds failed journeys on purpose, and a rubric about the resulting state would reject
good runs for them, as pairwise did.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.evaluation import CHARS_PER_TOKEN, outcome_of, render_journey
from app.judge_rubrics import RUBRIC_PROPOSAL, STUDY_KINDS
from app.models import Run, StudyRubric
from trajectory_contract import TrajectoryBundle

KINDS = tuple(STUDY_KINDS)
TITLE_CHARS, TEXT_CHARS = 80, 300
MIN_CRITERIA, MAX_CRITERIA = 2, 6
ANCHORS = ("5", "3", "1")
GROUP_SIZE = 4
# The normalised judge score at which a study rubric's verdict passes, to set against the code rubric's pass.
STUDY_PASS = 0.5
PREFIX = "study_"


class ProposalError(ValueError):
    """The judge's proposal could not be turned into a rubric."""


def _text(value, name: str, limit: int, *, strict: bool, required: bool = True) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if required and not text:
        raise ValueError(f"{name} is empty")
    if len(text) > limit:
        if strict:
            raise ValueError(f"{name} is longer than {limit} characters")
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def clean(fields: dict, *, strict: bool) -> dict:
    """A rubric's text, checked. Strict for an owner's edit, which is refused; lenient for the judge's proposal, which
    is trimmed to the limits."""
    if not isinstance(fields, dict):
        raise ValueError("a rubric is an object")
    raw = fields.get("criteria")
    if isinstance(raw, str):
        raw = [line.lstrip("-•* ").strip() for line in raw.splitlines()]
    if not isinstance(raw, list):
        raise ValueError("criteria is a list")
    criteria: list[str] = []
    for item in raw:
        text = _text(item, "a criterion", TEXT_CHARS, strict=strict, required=False)
        if text and text not in criteria:
            criteria.append(text)
    if len(criteria) > MAX_CRITERIA:
        if strict:
            raise ValueError(f"at most {MAX_CRITERIA} criteria")
        criteria = criteria[:MAX_CRITERIA]
    if len(criteria) < MIN_CRITERIA:
        raise ValueError(f"at least {MIN_CRITERIA} criteria")
    given = fields.get("anchors")
    if not isinstance(given, dict):
        raise ValueError("anchors give what a journey scoring 5, 3, and 1 shows")
    by_score = {str(key).strip(): value for key, value in given.items()}
    anchors = {score: _text(by_score.get(score), f"anchor {score}", TEXT_CHARS, strict=strict) for score in ANCHORS}
    return {
        "title": _text(fields.get("title"), "title", TITLE_CHARS, strict=strict),
        "description": _text(fields.get("description"), "description", TEXT_CHARS, strict=strict, required=False),
        "criteria": criteria,
        "anchors": anchors,
    }


def definition(rubric: StudyRubric, sector_label: str) -> dict:
    """The rubric as an engine definition. Its name carries a hash of the rest, so an edited rubric is a new one."""
    kind = rubric.kind
    lines = [
        f"You are an evaluation judge for synthetic customer journeys in a {sector_label.lower()} study. Score the "
        f"journey against this study's {kind} rubric on a 1-5 scale.",
        f"Rubric: {rubric.title}." + (f" {rubric.description}" if rubric.description else ""),
        "Criteria:",
        *[f"- {criterion}" for criterion in rubric.criteria],
        *[f"Score {score}: {rubric.anchors[score]}" for score in ANCHORS],
        'Output ONLY a single JSON object: {"score": integer 1-5, "reason": string}.',
    ]
    body = {
        "description": f"Study {kind} rubric: {rubric.title}"[:500],
        "system_prompt": "\n".join(lines),
        "user_prompt_template": "STUDY BRIEF AND QUESTION:\n{prompt}\n\nJOURNEY:\n{response}\n\nReturn your JSON verdict now.",
        "expected_keys": ["score", "reason"],
        "score": {"kind": "number", "key": "score", "min": 1, "max": 5},
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return {"name": f"{PREFIX}{kind}_{digest[:12]}", **body}


def rubric_out(rubric: StudyRubric, sector_label: str) -> dict:
    source = dict(rubric.source or {})
    raw = source.pop("raw", None)
    return {
        "id": rubric.id,
        "project_id": rubric.project_id,
        "kind": rubric.kind,
        "status": rubric.status,
        "title": rubric.title,
        "description": rubric.description,
        "criteria": list(rubric.criteria or []),
        "anchors": dict(rubric.anchors or {}),
        "source": source,
        "raw": (raw or "")[:4000],
        "edited": bool(rubric.edited),
        "engine_name": definition(rubric, sector_label)["name"],
        "approved_at": rubric.approved_at.isoformat() if rubric.approved_at else None,
        "created_at": rubric.created_at.isoformat(),
        "updated_at": rubric.updated_at.isoformat() if rubric.updated_at else None,
    }


def for_project(db: Session, project_id: str, *, statuses: tuple[str, ...] | None = None) -> list[StudyRubric]:
    query = select(StudyRubric).where(StudyRubric.project_id == project_id)
    if statuses:
        query = query.where(StudyRubric.status.in_(statuses))
    rows = list(db.scalars(query))
    order = {"approved": 0, "proposed": 1, "retired": 2}
    return sorted(rows, key=lambda row: (order.get(row.status, 3), KINDS.index(row.kind) if row.kind in KINDS else 9, row.created_at))


def engine_names(db: Session, project_id: str, sector_label: str) -> list[str]:
    """The engine names of a study's approved rubrics."""
    return sorted(definition(row, sector_label)["name"] for row in for_project(db, project_id, statuses=("approved",)))


def pending(db: Session, run: Run, cycle, sector_label: str) -> bool:
    """True when the study has approved rubrics that the run's latest cycle did not ask."""
    names = set(engine_names(db, run.project_id, sector_label))
    used = set(((cycle.judging or {}).get("study") or []) if cycle is not None else [])
    return bool(names - used)


def active_names(db: Session) -> set[str]:
    """The engine names of every approved study rubric, across studies; the rest may be removed from the engine."""
    from app.models import Project
    from sectors.registry import get_sector

    names = set()
    for row in db.scalars(select(StudyRubric).where(StudyRubric.status == "approved")):
        project = db.get(Project, row.project_id)
        label = get_sector(project.sector).label if project is not None else ""
        names.add(definition(row, label)["name"])
    return names


def _now() -> datetime:
    return datetime.now(timezone.utc)


def approve(db: Session, rubric: StudyRubric, account_id: str) -> None:
    for other in for_project(db, rubric.project_id, statuses=("approved",)):
        if other.kind == rubric.kind and other.id != rubric.id:
            other.status = "retired"
            other.updated_at = _now()
    rubric.status = "approved"
    rubric.approved_by = account_id
    rubric.approved_at = _now()
    rubric.updated_at = _now()


def edit(rubric: StudyRubric, fields: dict) -> None:
    """Apply an owner's edit. An edited rubric is proposed again, so what the judge asks is always what was approved."""
    current = {"title": rubric.title, "description": rubric.description, "criteria": rubric.criteria, "anchors": rubric.anchors}
    merged = clean({**current, **{key: value for key, value in fields.items() if value is not None}}, strict=True)
    for key, value in merged.items():
        setattr(rubric, key, value)
    rubric.status = "proposed"
    rubric.approved_by = None
    rubric.approved_at = None
    rubric.edited = 1
    rubric.updated_at = _now()


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------


def _order(seed: str, values: list[str]) -> list[str]:
    return sorted(values, key=lambda value: hashlib.sha256(f"{seed}|{value}".encode()).hexdigest())


def pick_group(found, seed: str, size: int = GROUP_SIZE) -> tuple[list[tuple[TrajectoryBundle, str]], str | None]:
    """The journeys the judge studies: a group with mixed outcomes when the run has groups, else a stratified few.

    Returns (bundle, trajectory id) pairs and the group's primary, or None when the journeys are separate.
    """
    from app.judging import sample_entries

    entries = found.entries()
    grouped = [entry for entry in entries if (entry.get("sequences") or 1) > 1]
    fallback = None
    for tid in _order(seed, [entry["trajectory_id"] for entry in grouped]):
        bundle = TrajectoryBundle.model_validate(found.journey(tid))
        members = [tid] + [item.trajectory_id for item in bundle.trajectories if item.parent_trajectory_id == tid]
        outcomes = {member: outcome_of(bundle, member) for member in members}
        chosen = (bundle, members, outcomes)
        if len(set(outcomes.values())) > 1:
            fallback = chosen
            break
        fallback = fallback or chosen
    if fallback is not None:
        bundle, members, outcomes = fallback
        # Alternate outcomes so a group larger than `size` still shows both.
        passing = [member for member in members if outcomes[member] == "pass"]
        other = [member for member in members if outcomes[member] != "pass"]
        mixed = [item for pair in zip(passing, other) for item in pair] + passing[len(other):] + other[len(passing):]
        return [(bundle, member) for member in mixed[:size]], members[0]
    picked = sample_entries(entries, size, seed)
    return [(TrajectoryBundle.model_validate(found.journey(entry["trajectory_id"])), entry["trajectory_id"]) for entry in picked], None


def propose(db: Session, run: Run, cfg, judge, *, register, progress=None) -> list[StudyRubric]:
    """Ask the judge for a solution and a behavior rubric from a group of the run's journeys, and store them as proposals,
    replacing the study's earlier unapproved proposals."""
    from app.judging import study_brief
    from app.store import DbStore, store_for
    from sectors.registry import get_sector
    from trajectory_contract import banking_fixture

    sector = get_sector(run.config["sector"])
    found = store_for(run) or DbStore(banking_fixture().model_dump(mode="json"))
    asked = len(for_project(db, run.project_id))
    members, group = pick_group(found, f"{run.id}|proposal|{asked}")
    if not members:
        raise ProposalError("This run has no journeys for the judge to study.")
    brief, chosen = study_brief(db, run, cfg, sector)
    budget = max(cfg.judge_prompt_tokens * CHARS_PER_TOKEN - len(brief) - 1600, 2000)
    texts = [render_journey(bundle, tid, budget // len(members))[0] for bundle, tid in members]
    response = "\n\n".join(f"--- Journey {index} of {len(texts)} ---\n{text}" for index, text in enumerate(texts, start=1))
    digest = register(RUBRIC_PROPOSAL)
    created = []
    for index, kind in enumerate(KINDS):
        if progress is not None:
            progress(index, len(KINDS), f"The judge is writing the {kind} rubric from {len(members)} journeys.")
        result = judge.run_eval(
            rubric=RUBRIC_PROPOSAL["name"],
            prompt=brief + "\n\n" + STUDY_KINDS[kind]["ask"],
            response=response,
            judge_model=cfg.inference_judge_model,
        )
        if not result.get("readable", True):
            raise ProposalError(f"The judge's {kind} rubric could not be read. Ask again.")
        try:
            fields = clean(result.get("parsed") or {}, strict=False)
        except ValueError as exc:
            raise ProposalError(f"The judge's {kind} rubric was incomplete: {exc}. Ask again.") from exc
        parsed = result.get("parsed") or {}
        created.append(
            StudyRubric(
                project_id=run.project_id,
                kind=kind,
                status="proposed",
                **fields,
                source={
                    "run_id": run.id,
                    "group": group,
                    "trajectory_ids": [tid for _, tid in members],
                    "outcomes": {tid: outcome_of(bundle, tid) for bundle, tid in members},
                    "reference": sorted({item["source"] for item in chosen}),
                    "judge_model": result.get("judge_model") or cfg.inference_judge_model,
                    "fit": parsed.get("fit"),
                    "proposal_digest": result.get("rubric_digest") or digest,
                    "raw": result.get("raw", ""),
                },
            )
        )
    # An owner's edited proposal stays; only untouched proposals give way to the new ones.
    db.execute(delete(StudyRubric).where(StudyRubric.project_id == run.project_id, StudyRubric.status == "proposed", StudyRubric.edited == 0))
    db.add_all(created)
    db.commit()
    return created
