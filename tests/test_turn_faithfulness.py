"""Written turns checked for meaning (Slice 12, decision 14): sampled each cycle, judged against the facts each had to
state, and put back to their templates when every readable repeat calls them unfaithful."""

import json

import pytest

from app import runtime
from app.evaluation import _faithfulness
from app.faithfulness import facts, sample_turns, written_turns
from app.judge_rubrics import TURN_FAITHFULNESS
from app.store import DbStore
from sectors.registry import get_sector
from test_api import _auth, _link, _project, _ready_key, _run
from test_provider_text import SETTINGS, FakeWriterAgent, Recorder

BANKING = get_sector("banking")
# An outcome no skeleton gives: the code checks cannot see it, and a judge should.
PLANTED = " and the account was then closed"


def _assistant(sequence):
    return [segment for context in sequence["contexts"] for segment in context["segments"] if segment["role"] == "assistant"]


# ---------------------------------------------------------------------------
# Finding and sampling written turns
# ---------------------------------------------------------------------------


def test_written_turns_carry_the_events_they_report_and_the_template_they_replaced():
    plain = BANKING.generate(**SETTINGS).model_dump(mode="json")
    written = BANKING.generate(**SETTINGS, writer=Recorder()).model_dump(mode="json")
    turns = written_turns(written)
    assert len(turns) == sum(len(_assistant(sequence)) for sample in written["samples"] for sequence in sample["sequences"])
    templates = {segment["segment_id"]: segment["text"] for sample in plain["samples"] for sequence in sample["sequences"] for segment in _assistant(sequence)}
    assert all(turn["template"] == templates[turn["segment_id"]] and turn["text"].startswith("As an update,") for turn in turns)
    events = {event["event_id"]: event["event_type"] for event in written["events"]}
    for trajectory in written["trajectories"]:
        mine = [turn for turn in turns if turn["trajectory_id"] == trajectory["trajectory_id"]]
        assert [name for turn in mine for name in turn["events"]] == [events[item] for item in trajectory["event_ids"]]
    assert facts(turns[0]).startswith("Events, in order: ") and turns[0]["template"] in facts(turns[0])
    assert written_turns(plain) == []


def test_a_sample_is_seeded_and_spreads_over_sequences_first():
    store = DbStore(BANKING.generate(**SETTINGS, writer=Recorder()).model_dump(mode="json"))
    picked = sample_turns(store, 5, "cycle-1")
    assert len(picked) == 5 and len({turn["sequence_id"] for turn in picked}) == 5
    assert picked == sample_turns(store, 5, "cycle-1") and picked != sample_turns(store, 5, "cycle-2")
    assert sample_turns(store, 0, "cycle-1") == [] and sample_turns(None, 5, "cycle-1") == []


def test_the_faithfulness_rubric_is_a_valid_engine_definition():
    template = TURN_FAITHFULNESS["user_prompt_template"]
    assert all(marker in template for marker in ("{expected}", "{prompt}", "{response}")) and template.count("{") == 3
    assert TURN_FAITHFULNESS["score"] == {"kind": "boolean", "key": "faithful"} and TURN_FAITHFULNESS["requires_expected"] is True
    assert TURN_FAITHFULNESS["expected_keys"] == ["faithful"]


# ---------------------------------------------------------------------------
# What counts as unfaithful
# ---------------------------------------------------------------------------


def _turn(segment_id):
    return {"segment_id": segment_id, "sequence_id": "Q1", "trajectory_id": "T1", "batch": None, "events": ["account.opened"],
            "text": "Your account is open.", "template": "The account was opened.", "written_by": "provider:m"}


def _v(segment_id, score, model, readable=True, reason=""):
    return {"rubric": "turn_faithfulness", "segment_id": segment_id, "score": score, "judge_model": model, "readable": readable,
            "parsed": {"reason": reason}, "raw": ""}


def test_a_turn_goes_back_only_when_every_readable_repeat_of_every_judge_calls_it_unfaithful():
    verdicts = [
        *[_v("G1", 0.0, model, reason="adds a closure") for model in ("a", "b") for _ in range(3)],
        # One judge's repeat finds this turn faithful, so it stays, though both judges lean unfaithful.
        _v("G2", 0.0, "a"), _v("G2", 0.0, "a"), _v("G2", 0.0, "a"), _v("G2", 1.0, "b"), _v("G2", 0.0, "b"), _v("G2", 0.0, "b"),
        # Unreadable verdicts are no evidence either way.
        *[_v("G3", 0.0, model, readable=False) for model in ("a", "b")],
        # An unreadable repeat beside unfaithful ones does not save a turn.
        _v("G4", 0.0, "a"), _v("G4", 0.0, "a", readable=False), _v("G4", 0.0, "b"),
        *[_v("G5", 1.0, model) for model in ("a", "b")],
    ]
    flags = []
    found = _faithfulness(verdicts, [_turn(item) for item in ("G1", "G2", "G3", "G4", "G5")], ["a", "b"], flags)
    assert found["revert"] == ["G1", "G4"] and found["turns"] == 5
    rows = {row["segment_id"]: row for row in found["rows"]}
    assert rows["G1"]["reason"] == "adds a closure" and rows["G1"]["judges"] == {"a": {"repeats": 3, "faithful": 0}, "b": {"repeats": 3, "faithful": 0}}
    assert rows["G2"]["judges"]["b"] == {"repeats": 3, "faithful": 1} and rows["G3"]["judges"] == {}
    assert found["by_model"] == {"a": {"turns": 4, "unfaithful": 3}, "b": {"turns": 4, "unfaithful": 3}}
    assert sorted((flag["model"], flag["kind"]) for flag in flags) == sorted([(model, "unfaithful_turn") for model in ("a", "b") for _ in range(3)])


# ---------------------------------------------------------------------------
# A cycle with a planted unfaithful turn
# ---------------------------------------------------------------------------


class PlantingAgent(FakeWriterAgent):
    """Writes every turn faithfully, except the turns `plant` picks, where it adds an outcome the facts never give."""

    def __init__(self, plant, **kwargs):
        super().__init__(**kwargs)
        self.plant, self.written = plant, 0

    def write(self, system, prompt, tokens=8000):
        answer = json.loads(super().write(system, prompt, tokens))
        for position, sequence in enumerate(answer["sequences"]):
            for index, turn in enumerate(sequence["turns"]):
                if self.plant(self.written, position, index, turn):
                    turn[-1]["text"] = turn[-1]["text"].rstrip(".") + PLANTED + "."
        self.written += 1
        return json.dumps(answer)


class FaithfulnessJudge:
    """Passes every journey, and calls a written turn unfaithful exactly when it holds the planted outcome."""

    def __init__(self):
        self.registered, self.asked = [], []

    def register_rubric(self, definition):
        self.registered.append(definition["name"])
        return {"name": definition["name"], "digest": "sha256:" + definition["name"]}

    def run_eval(self, *, rubric, judge_model=None, repeats=1, temperature=0.0, **payload):
        if rubric == "turn_faithfulness":
            self.asked.append(payload)
            faithful = PLANTED.strip() not in payload["response"]
            score, reason = (1.0, "states its events") if faithful else (0.0, "adds a closure the facts never give")
        else:
            score, reason = {"helpfulness": 5}.get(rubric, 1.0), "fine"
        verdicts = [{"score": score, "parsed": {"reason": reason}, "raw": "{}", "readable": True}] * repeats
        return {**verdicts[0], "verdicts": verdicts, "judge_model": judge_model, "duration_ms": 1}


def _written_run(client, email, plant, **options):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    runtime.agent_factory = lambda provider, key, model: PlantingAgent(plant, provider=provider, key=key, model=model)
    try:
        run = _run(client, headers, project_id, credential_id, event_budget=None, group_size=2, provider_text=True, provider_model="text-model", **options)
    finally:
        runtime.agent_factory = None
    assert run.status_code == 200, run.text
    return headers, run.json()


def _segments(bundle):
    return {segment["segment_id"]: segment for sample in bundle["samples"] for sequence in sample["sequences"] for segment in _assistant(sequence)}


def test_a_cycle_catches_a_planted_unfaithful_turn_and_puts_its_template_back(client, monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")
    monkeypatch.setenv("JUDGE_TEXT_SAMPLE", "50")
    # The planted outcome goes into the first turn of the first sequence only.
    headers, run = _written_run(client, "faithful-cycle@example.com", lambda call, position, index, turn: (call, position, index) == (0, 0, 0),
                                target_trajectory_count=6, max_assistant_turns=2)
    before = _segments(run["bundle"])
    planted = [segment_id for segment_id, segment in before.items() if PLANTED in segment["text"]]
    assert len(planted) == 1 and run["generation"]["text"]["written"] == len(before)
    judge = runtime.judge = FaithfulnessJudge()
    response = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert response.status_code == 200, response.text
    cycle = response.json()["cycles"][0]
    assert "turn_faithfulness" in judge.registered and cycle["judging"]["rubrics"]["turn_faithfulness"]["source"] == "tenant"
    found = cycle["agreement"]["faithfulness"]
    # Every written turn fits the sample, and only the planted one goes back.
    assert found["turns"] == len(before) and found["revert"] == planted == cycle["judging"]["faithfulness"]["reverted"]
    assert all(row["reason"] == "adds a closure the facts never give" for row in found["rows"] if row["revert"])
    assert {(flag["kind"], flag["model"]) for flag in cycle["flags"] if flag["rubric"] == "turn_faithfulness"} == {
        ("unfaithful_turn", model) for model in cycle["models"]
    }
    marked = [item for item in cycle["verdicts"] if item["rubric"] == "turn_faithfulness"]
    assert len(marked) == len(before) * len(cycle["models"]) * 3 and all(item["segment_id"] in before for item in marked)
    # The judge reads the facts the turn had to state: its events and the template it replaced.
    asked = next(item for item in judge.asked if PLANTED.strip() in item["response"])
    assert before[planted[0]]["template"] in asked["expected"] and asked["expected"].startswith("Events, in order: ")
    # A faithfulness verdict decides nothing about the run.
    assert cycle["accepted"] is True

    after = client.get(f"/runs/{run['id']}", headers=headers).json()
    segments = _segments(after["bundle"])
    reverted = segments[planted[0]]
    assert reverted["text"] == before[planted[0]]["template"] and reverted["written_by"] is None and reverted["template"] is None
    assert all(segments[key] == value for key, value in before.items() if key not in planted)
    assert after["generation"]["text"]["reverted"] == 1
    # A reverted turn is a template again, so no later cycle reads it as written.
    assert len(written_turns(after["bundle"])) == len(before) - 1


def test_a_large_run_puts_turns_back_in_its_batch_files(client, monkeypatch):
    import app.generation as generation
    from app import store

    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")
    monkeypatch.setenv("JUDGE_TEXT_SAMPLE", "12")
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    # Three writer calls write six sequences of two turns, so the sample holds every written turn whatever the run's id
    # seeds, and the first turn of each holds the planted outcome.
    headers, run = _written_run(client, "faithful-large@example.com", lambda call, position, index, turn: index == 0, target_trajectory_count=40,
                                max_assistant_turns=2, provider_text_budget=3)
    assert run["generation"]["storage"]["batches"] > 1 and run["generation"]["text"]["written"] == 12
    runtime.judge = FaithfulnessJudge()
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    rows = cycle["agreement"]["faithfulness"]["rows"]
    assert len(rows) == 12 and {row["revert"] for row in rows} == {True, False}
    assert all(row["revert"] == (PLANTED in row["text"]) for row in rows)
    reverted = set(cycle["judging"]["faithfulness"]["reverted"])
    assert reverted == {row["segment_id"] for row in rows if row["revert"]}
    files = store.FileStore(run["id"])
    segments = {segment["segment_id"]: segment for name in files.batches() for segment in _segments(files.batch(name)).values()}
    for row in rows:
        segment = segments[row["segment_id"]]
        if row["revert"]:
            assert segment["text"] == row["template"] and segment["written_by"] is None
        else:
            assert segment["text"] == row["text"] and segment["written_by"] == "provider:text-model"
