from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from app.judge import Judge
from sectors.registry import get_sector

DEFAULT_THRESHOLDS = {
    "helpfulness": 3.0,
    "correctness": 0.5,
    "safety": 1.0,
    "pairwise_quality": 0.5,
}


def render_trajectory(bundle: TrajectoryBundle, trajectory_id: str) -> str:
    traj = next(item for item in bundle.trajectories if item.trajectory_id == trajectory_id)
    events = {event.event_id: event for event in bundle.events}
    links: dict[str, list[str]] = {}
    for link in bundle.event_objects:
        links.setdefault(link.event_id, []).append(f"{link.object_id}:{link.object_role}")
    lines = [f"trajectory {traj.trajectory_id} ({traj.observed_or_synthetic})"]
    for event_id in traj.event_ids:
        event = events[event_id]
        joined = ", ".join(links.get(event_id, []))
        lines.append(f"{event.event_time.isoformat()} {event.event_type} [{joined}]")
    return "\n".join(lines)


def corpus_excerpt(items: list) -> str:
    parts = [f"{item.kind}: {item.name}" for item in items]
    return "\n".join(parts)


def evaluate_bundle(
    *,
    bundle: TrajectoryBundle,
    sector_id: str,
    sub_domains: list[str],
    language: str,
    cold_start: bool,
    corpus_items: list,
    thresholds: dict[str, float],
    judge: Judge,
) -> dict:
    sector = get_sector(sector_id)
    errors = sector.hard_checks(bundle)
    brief = sector.judge_brief(
        sub_domains=sub_domains,
        language=language,
        corpus_excerpt=corpus_excerpt(corpus_items),
        cold_start=cold_start,
    )
    reference_quality = "weak" if cold_start else "corpus"
    if errors:
        return {
            "hard_check_passed": False,
            "hard_check_errors": errors,
            "reference_quality": reference_quality,
            "accepted": False,
            "revision_notes": errors,
            "verdicts": [],
            "called_judge": False,
        }

    parent = next((item for item in bundle.trajectories if item.parent_trajectory_id is None), bundle.trajectories[0])
    parent_text = render_trajectory(bundle, parent.trajectory_id)
    alternative = next((item for item in bundle.trajectories if item.parent_trajectory_id), None)
    calls: list[tuple[str, dict]] = [
        (
            "helpfulness",
            {
                "prompt": brief + "\nScore how representative this banking journey is.",
                "response": parent_text,
            },
        ),
        (
            "correctness",
            {
                "prompt": "Do these events follow the banking transitions in the reference?",
                "response": parent_text,
                "expected": brief,
            },
        ),
        (
            "safety",
            {
                "prompt": "Confirm the trajectory is synthetic and contains no real personal or account identifiers.",
                "response": parent_text,
            },
        ),
    ]
    if alternative is not None:
        calls.append(
            (
                "pairwise_quality",
                {
                    "prompt": brief + "\nCompare the parent journey (A) with the alternative branch (B).",
                    "response": parent_text,
                    "response_b": render_trajectory(bundle, alternative.trajectory_id),
                },
            )
        )

    verdicts = []
    notes: list[str] = []
    accepted = True
    for rubric, payload in calls:
        result = judge.run_eval(rubric=rubric, **payload)
        score = float(result["score"])
        minimum = float(thresholds.get(rubric, DEFAULT_THRESHOLDS[rubric]))
        if score < minimum:
            accepted = False
            justification = ""
            parsed = result.get("parsed") or {}
            if isinstance(parsed, dict):
                justification = str(parsed.get("justification") or parsed.get("reason") or result.get("raw") or "")
            notes.append(f"{rubric} {score} below {minimum}. {justification}".strip())
        verdicts.append({"rubric": rubric, "score": score, **{k: result[k] for k in ("parsed", "raw", "judge_model", "duration_ms")}})

    return {
        "hard_check_passed": True,
        "hard_check_errors": [],
        "reference_quality": reference_quality,
        "accepted": accepted,
        "revision_notes": notes,
        "verdicts": verdicts,
        "called_judge": True,
    }
