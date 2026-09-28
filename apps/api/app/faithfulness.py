"""Provider-written turns checked for meaning (Slice 12, decision 14).

Code checks every written turn for its events, amounts, identifiers, and language, but a turn can pass them and still
change what happened. Each cycle samples written turns from the run, and the judge reads each against the facts it had
to state: its events in order and the template it replaced. A turn that every readable repeat of every judge calls
unfaithful goes back to its template in the stored run. A false alarm costs a template sentence, not data.
"""

from __future__ import annotations

import copy
import hashlib

from sqlalchemy.orm.attributes import flag_modified

from app.store import DbStore, batch_path, write_json
from sectors.turn_text import _groups

# Flags that come from a turn's text; a reverted turn's are found again from its template.
TEXT_FLAGS = {"empty_turn", "repeated_turn", "overlong_turn"}
# A batch run is read batch by batch until this many candidates per sampled turn are in hand.
CANDIDATES_PER_TURN = 8


def written_turns(bundle: dict, batch: str | None = None) -> list[dict]:
    """Every written assistant turn of a bundle, with the events it reports and the template it replaced."""
    events = {event["event_id"]: event["event_type"] for event in bundle["events"]}
    trajectories = {item["trajectory_id"]: item for item in bundle["trajectories"]}
    found = []
    for sample in bundle["samples"]:
        for sequence in sample["sequences"]:
            assistant = [segment for context in sequence["contexts"] for segment in context["segments"] if segment["role"] == "assistant"]
            if not any(segment.get("written_by") and segment.get("template") for segment in assistant):
                continue
            trajectory = trajectories.get(sequence.get("trajectory_id") or "")
            types = [events[item] for item in trajectory["event_ids"] if item in events] if trajectory else []
            # The events of each turn, split as the template split its sentences.
            parts = _groups(len(types), len(assistant))
            for index, segment in enumerate(assistant):
                if not (segment.get("written_by") and segment.get("template")):
                    continue
                found.append({
                    "segment_id": segment["segment_id"],
                    "sequence_id": sequence["sequence_id"],
                    "trajectory_id": sequence.get("trajectory_id"),
                    "batch": batch,
                    "events": [types[item] for item in parts[index]] if index < len(parts) else [],
                    "text": segment["text"],
                    "template": segment["template"],
                    "written_by": segment["written_by"],
                })
    return found


def _rank(seed: str, key: str) -> str:
    return hashlib.sha256(f"{seed}|{key}".encode()).hexdigest()


def sample_turns(store, size: int, seed: str) -> list[dict]:
    """Up to `size` written turns in a seeded order, one per sequence before a second from any."""
    if size <= 0 or store is None:
        return []
    if isinstance(store, DbStore):
        candidates = written_turns(store.bundle)
    else:
        candidates = []
        for name in sorted(store.batches(), key=lambda name: _rank(seed, name)):
            candidates += written_turns(store.batch(name), name)
            if len(candidates) >= size * CANDIDATES_PER_TURN:
                break
    candidates.sort(key=lambda turn: _rank(seed, turn["segment_id"]))
    first, rest, seen = [], [], set()
    for turn in candidates:
        (rest if turn["sequence_id"] in seen else first).append(turn)
        seen.add(turn["sequence_id"])
    return (first + rest)[:size]


def facts(turn: dict) -> str:
    """What the turn had to state, as the judge reads it."""
    lines = [f"Events, in order: {', '.join(turn['events'])}."] if turn["events"] else []
    lines.append(f"Template: {turn['template']}")
    return "\n".join(lines)


def _revert_in(bundle: dict, segment_ids: set[str]) -> list[str]:
    from sectors.journeys import _flag
    from trajectory_contract.models import Segment

    done = []
    for sample in bundle["samples"]:
        for sequence in sample["sequences"]:
            for context in sequence["contexts"]:
                touched = False
                for segment in context["segments"]:
                    if segment["segment_id"] in segment_ids and segment.get("template"):
                        segment.update(text=segment["template"], template=None, written_by=None)
                        done.append(segment["segment_id"])
                        touched = True
                if touched:
                    # Flags found from the written text are found again from the template's.
                    models = [Segment.model_validate({**segment, "flagged_reason": None if segment.get("flagged_reason") in TEXT_FLAGS else segment.get("flagged_reason")}) for segment in context["segments"]]
                    _flag(models)
                    for segment, model in zip(context["segments"], models):
                        segment["flagged_reason"] = model.flagged_reason
    return done


def revert(run, store, turns: list[dict]) -> list[str]:
    """Put these written turns back to their templates in the stored run, and return the ids reverted."""
    if not turns or store is None:
        return []
    ids = {turn["segment_id"] for turn in turns}
    done: list[str] = []
    if isinstance(store, DbStore):
        done = _revert_in(store.bundle, ids)
        if done and store.bundle is run.candidate:
            flag_modified(run, "candidate")
    else:
        for name in sorted({turn["batch"] for turn in turns if turn["batch"]}):
            bundle = copy.deepcopy(store.batch(name))
            found = _revert_in(bundle, ids)
            if found:
                write_json(batch_path(store.root, name), bundle)
                done += found
    if done and run.generation and isinstance(run.generation.get("text"), dict):
        run.generation = {**run.generation, "text": {**run.generation["text"], "reverted": run.generation["text"].get("reverted", 0) + len(done)}}
        if isinstance(store, DbStore) and store.bundle is run.candidate and isinstance((run.candidate.get("generation") or {}).get("text"), dict):
            run.candidate["generation"]["text"]["reverted"] = run.generation["text"]["reverted"]
    return done
