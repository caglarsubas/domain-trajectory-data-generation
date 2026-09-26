"""Evaluation tasks: each prompt and each episode as a task with its environment, verifiers, and reference trajectories.

A journey task is a prompt whose group holds k attempts by the generator's policy, scored by the five
signal verifiers in `sectors.scorers`. An agent task is an episode: the mock bank's state, operations,
and answers, verified by the episode rubric, with the scripted rollouts and any provider models'
rollouts as attempts. Results are reported as avg@k, the mean score of an attempt, and pass@k, the
unbiased estimate of at least one pass in k attempts, averaged over the tasks with at least k attempts.
"""

from __future__ import annotations

from sectors.episodes import passes
from sectors.scorers import PASS_AT, SIGNALS, VERIFIERS, pass_at_k

EPISODE_VERIFIERS = {
    "format": "Exactly one operation call whose arguments the operation's schema accepts.",
    "legality": "The operation records one of the environment's legal events.",
    "grounding": "Every identifier argument names one of the case's own objects.",
    "decision": "The recorded event is one after which the journey reaches its goal (passing_events).",
    "report": "A final message says what was recorded or why it was refused.",
}


def _results(attempts: list[tuple[float, bool]]) -> dict:
    """Mean score, passes, and pass@k over one task's attempts."""
    n = len(attempts)
    passes = sum(1 for _, passed in attempts if passed)
    return {
        "attempts": n,
        "passes": passes,
        "mean": round(sum(score for score, _ in attempts) / n, 4) if n else None,
        "pass_at_k": {str(k): round(pass_at_k(n, passes, k), 4) for k in PASS_AT if k <= n},
    }


def journey_task(sample: dict, split: str, paths: dict[str, dict], primary_signal: str) -> dict:
    """A prompt as a task: its opening, the group's attempts as reference trajectories, and each signal's results."""
    first = sample["sequences"][0]["contexts"][0]["segments"]
    opening = next((segment["text"] for segment in first if segment["role"] == "user"), "")
    references = []
    by_signal: dict[str, list[tuple[float, bool]]] = {}
    for sequence in sample["sequences"]:
        signals = sequence.get("signals") or {"outcome": {"score": 1.0 if sequence.get("outcome") == "pass" else 0.0, "passed": sequence.get("outcome") == "pass"}}
        path = paths.get(sequence.get("trajectory_id") or "", {"events": [], "times": []})
        references.append({
            "sequence_id": sequence["sequence_id"],
            "trajectory_id": sequence.get("trajectory_id"),
            "events": path["events"],
            "times": path["times"],
            "signals": {name: {"score": verdict["score"], "passed": verdict["passed"]} for name, verdict in signals.items()},
        })
        for name, verdict in signals.items():
            by_signal.setdefault(name, []).append((verdict["score"], verdict["passed"]))
    return {
        "task_id": f"{sample['sample_id']}.JT",
        "kind": "journey",
        "sample_id": sample["sample_id"],
        "split": split,
        "prompt": sample["prompt"],
        "opening": opening,
        "environment": "environment",
        "verifiers": [name for name in SIGNALS if name in by_signal],
        "primary_verifier": primary_signal,
        "references": references,
        "results": {"generator": {name: _results(values) for name, values in by_signal.items()}},
    }


def episode_task(episode: dict, split: str) -> dict:
    """An episode as an agent task: the mock bank, the rubric's verifiers, reference rollouts, and each policy's results."""
    skeleton = episode["skeleton"]
    observed = [rollout for rollout in episode["rollouts"] if rollout["policy"] in {"reference", "alternative"}]
    responses = {rollout["action_event"]: rollout["turns"][3]["tool_result"] for rollout in observed}
    by_policy: dict[str, list[tuple[float, bool]]] = {}
    for rollout in episode["rollouts"]:
        by_policy.setdefault(rollout["policy"], []).append((rollout.get("reward") or 0.0, passes(rollout)))
    return {
        "task_id": f"{episode['episode_id']}.AT",
        "kind": "agent_episode",
        "episode_id": episode["episode_id"],
        "sample_id": episode.get("sample_id"),
        "split": split,
        "environment": {
            "system": episode["rollouts"][0]["turns"][0]["text"] if episode["rollouts"] else None,
            "task": episode["task"],
            "state": episode["state"],
            "tools": episode["tools"],
            "objects": skeleton["objects"],
            "legal_events": skeleton["legal_events"],
            # What the mock bank answers for each legal step a journey took; anything else is refused.
            "responses": responses,
        },
        "verifiers": {
            "format": {"tools": [tool["name"] for tool in episode["tools"]]},
            "legality": {"legal_events": skeleton["legal_events"]},
            "grounding": {"objects": sorted(set(skeleton["objects"].values()))},
            "decision": {"passing_events": sorted({rollout["action_event"] for rollout in observed if rollout["outcome"] == "pass"})},
            "report": {},
        },
        "references": [{"policy": rollout["policy"], "action_event": rollout["action_event"], "outcome": rollout["outcome"], "turns": rollout["turns"]} for rollout in observed],
        "results": {policy: _results(values) for policy, values in by_policy.items()},
    }


class Report:
    """avg@k and pass@k over every task an export writes, by signal for journeys and by policy for agent tasks."""

    def __init__(self) -> None:
        self.journeys: dict[str, dict] = {}
        self.agents: dict[str, dict] = {}
        self.counts = {"journey": 0, "agent_episode": 0}

    @staticmethod
    def _add(table: dict[str, dict], name: str, result: dict) -> None:
        row = table.setdefault(name, {"tasks": 0, "attempts": 0, "score": 0.0, "pass_at_k": {}, "tasks_at_k": {}})
        row["tasks"] += 1
        row["attempts"] += result["attempts"]
        row["score"] += result["mean"] or 0.0
        for k, value in result["pass_at_k"].items():
            row["pass_at_k"][k] = row["pass_at_k"].get(k, 0.0) + value
            row["tasks_at_k"][k] = row["tasks_at_k"].get(k, 0) + 1

    def add(self, task: dict) -> None:
        self.counts[task["kind"]] += 1
        table = self.journeys if task["kind"] == "journey" else self.agents
        results = task["results"]["generator"] if task["kind"] == "journey" else task["results"]
        for name, result in results.items():
            self._add(table, name, result)

    @staticmethod
    def _summary(table: dict[str, dict]) -> dict:
        return {
            name: {
                "tasks": row["tasks"],
                "attempts": row["attempts"],
                "avg@k": round(row["score"] / row["tasks"], 4),
                "pass@k": {k: round(value / row["tasks_at_k"][k], 4) for k, value in sorted(row["pass_at_k"].items(), key=lambda item: int(item[0]))},
            }
            for name, row in table.items()
        }

    def as_dict(self, *, sector, config: dict, primary_signal: str) -> dict:
        lifecycle = sector.lifecycle
        pack = getattr(sector, "pack", None)
        return {
            "format": "trajectory-studio-evaluation",
            "format_version": 1,
            "tasks": dict(self.counts),
            "journeys": {
                "policy": "generator",
                "primary_verifier": primary_signal,
                "by_verifier": self._summary(self.journeys),
                "note": "Each prompt's group is k attempts by the generator's policy: a reference for how hard each task is.",
            },
            "agent_episodes": {
                "by_policy": self._summary(self.agents),
                "scripted_policies": ["reference", "alternative", "perturbed:illegal", "perturbed:wrong_object"],
                "note": "Scripted policies are fixed steps, not samples; provider:<model> rows are a model's own attempts on your key.",
            },
            "metrics": {
                "avg@k": "The mean score of an attempt, averaged over tasks: signal scores for journeys, rewards for agent tasks.",
                "pass@k": "1 - C(n-c, k) / C(n, k) for a task with n attempts and c passes, averaged over the tasks with at least k attempts.",
            },
            "verifiers": {
                "journey": {name: VERIFIERS[name] for name in SIGNALS},
                "agent_episode": {**EPISODE_VERIFIERS, "pass": "format, legality, grounding, and decision all hold"},
                "implementation": "sectors.scorers.Scorer and sectors.episodes; each reads only event types, times, and calls, so the same code verifies a model's attempt.",
            },
            "environment": {
                "environment_id": "environment",
                "sector": sector.id,
                "pack_version": getattr(pack, "pack_version", None) or config.get("pack_version"),
                "sub_domains": config.get("sub_domains"),
                "language": config.get("language"),
                "goal": (getattr(pack, "goal", {}) or {}).get("en"),
                "milestones": {domain: list(events) for domain, events in lifecycle.milestones.items()},
                "events": [
                    {
                        "event_type": spec.event_type,
                        "sub_domains": list(spec.sub_domains),
                        "requires": [f"{guard.kind}.{guard.dimension} in {{{', '.join(state or 'unset' for state in guard.states)}}}" for guard in spec.requires],
                        "sets": [f"{effect.kind}.{effect.dimension} = {effect.state}" for effect in spec.sets],
                        "repeat": spec.repeat,
                        "outcome": spec.outcome,
                        "ends_journey": spec.ends_journey,
                        "dwell_hours": list(spec.dwell_hours),
                        "prior_weight": spec.weight,
                    }
                    for spec in lifecycle.events
                ],
            },
        }
