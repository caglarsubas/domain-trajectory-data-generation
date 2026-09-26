import gzip
import hashlib
import json

import pytest

from sectors.banking.spec import success
from sectors.decisions import VALUE_SAMPLES, record_count, records
from sectors.lifecycle import apply
from sectors.registry import get_sector
from test_api import _auth, _project, _run
from trajectory_contract import DECISION_SCHEMA_VERSION, DecisionRecord

BANKING = get_sector("banking")
SETTINGS = dict(
    sub_domains=["onboarding_and_kyc", "consumer_credit", "cards_and_payments"], language="en", target_trajectory_count=24, event_budget=None,
    min_events=6, max_events=18, max_assistant_turns=6, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="decision_score",
    consumer="decision_scoring", target_family="jev", corpus_text="", feedback=None, revision_notes=None, parent_bundle=None, seed="decisions-test",
)
OPEN = {"allow_unaccepted": "true"}


@pytest.fixture(scope="module")
def bundle():
    return BANKING.generate(**SETTINGS, decisions=True)


def _schema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    from app.export import decision_schema

    return jsonschema.Draft202012Validator(json.loads(decision_schema()))


# Decision points


def test_each_first_outcome_decision_is_recorded_with_what_was_known(bundle):
    lifecycle = BANKING.lifecycle
    events = {event.event_id: event for event in bundle.events}
    assert bundle.decisions and bundle.generation.decisions["points"] == len(bundle.decisions)
    seen = set()
    for point in bundle.decisions:
        primary = next(item for item in bundle.trajectories if item.trajectory_id == point.trajectory_id)
        assert primary.parent_trajectory_id is None
        types = [events[item].event_type for item in primary.event_ids]
        assert primary.event_ids[point.decision_index] == point.decision_event_id and types[point.decision_index] == point.taken
        assert point.history == types[: point.decision_index] and lifecycle[point.taken].outcome == point.group
        # Only the first decision of each group in a journey.
        assert (point.trajectory_id, point.group) not in seen
        seen.add((point.trajectory_id, point.group))
        state: dict = {}
        for name in point.history:
            apply(lifecycle[name], state)
        assert point.state == {f"{kind}.{dimension}": value for (kind, dimension), value in sorted(state.items())}
        assert point.facts["events_so_far"] == point.decision_index and point.facts["last_event"] == point.history[-1]
        assert sum(value for key, value in point.facts.items() if key.startswith("times.")) == point.decision_index
        assert point.outcome["journey_success"] == success(types) and point.counterfactual_basis == "generator_policy"
        assert point.sample_id and point.value_samples == VALUE_SAMPLES and point.goal.startswith("The journey ends")


def test_targets_follow_the_generator_policy_and_values_come_from_simulation(bundle):
    lifecycle = BANKING.lifecycle
    for point in bundle.decisions:
        weights = {option.option_id: lifecycle[option.event_type].weight for option in point.options}
        total = sum(weights.values())
        assert {option.option_id: option.probability for option in point.options} == {name: round(weight / total, 4) for name, weight in weights.items()}
        assert point.taken in weights and len(point.options) > 1
        for option in point.options:
            assert 0.0 <= option.value <= 1.0
            # An outcome the pack counts as a failure can never reach the goal.
            if option.event_type in {"application.declined", "application.abandoned", "kyc.failed"}:
                assert option.value == 0.0
    approvals = [option.value for point in bundle.decisions for option in point.options if option.event_type == "application.approved"]
    assert approvals and max(approvals) > 0.0


def test_a_calibrated_run_takes_its_targets_from_the_calibrated_policy():
    # A data source where half of all verified KYC cases are declined pulls the policy share far from the prior.
    calibration = {"transitions": {"kyc.passed": {"application.approved": 60, "application.declined": 60}}, "cases": 120, "sources": ["log.csv"]}
    found = BANKING.generate(**{**SETTINGS, "start_mode": "warm"}, calibration=calibration, decisions=True)
    decided = [point for point in found.decisions if point.group == "application_decision" and point.history[-1] == "kyc.passed"]
    assert decided
    for point in decided:
        declined = next(option for option in point.options if option.event_type == "application.declined")
        weights = {option.event_type: BANKING.lifecycle[option.event_type].weight for option in point.options}
        prior = weights["application.declined"] / sum(weights.values())
        assert declined.probability > prior + 0.2


def test_recording_decisions_is_reproducible_and_changes_no_journey(bundle):
    from sectors import decisions

    # Values are seeded by the policy and the context, so they come out the same without the cache.
    decisions._CACHE.clear()
    again = BANKING.generate(**SETTINGS, decisions=True)
    plain = BANKING.generate(**SETTINGS)
    assert [point.model_dump() for point in again.decisions] == [point.model_dump() for point in bundle.decisions]
    assert [item.event_ids for item in plain.trajectories] == [item.event_ids for item in bundle.trajectories]
    assert plain.decisions == [] and plain.generation.decisions is None
    # Consumer and target family choose export parts; they are no longer written into the prompt.
    system = bundle.samples[0].sequences[0].contexts[0].segments[0].text
    assert "Consumer" not in system and "Target family" not in system


def test_the_insurance_pack_records_its_own_decisions():
    insurance = get_sector("insurance")
    found = insurance.generate(**{**SETTINGS, "sub_domains": list(insurance.sub_domains)}, decisions=True)
    groups = {point.group for point in found.decisions}
    assert groups & {"underwriting_outcome", "claim_decision"} and all(point.goal.startswith("The journey ends") for point in found.decisions)


# Typed records


def test_a_decision_becomes_choice_true_false_and_score_records_with_variants(bundle):
    validator = _schema_validator()
    point = next(item for item in bundle.decisions if item.distractor)
    found = records(point, split="validation", language="en")
    assert len(found) == record_count(point) == 3 * (3 + len(point.options))
    for record in found:
        validator.validate(record.model_dump(mode="json"))
        assert record.schema_version == DECISION_SCHEMA_VERSION and record.split == "calibration"
        assert record.abstain["option_id"] == "abstain" and "abstain" not in (record.target_distribution or {})
        assert "abstain" in record.prompt or record.question_type == "score"
    by_id = {record.record_id: record for record in found}
    for record in found:
        if record.variant == "original":
            continue
        original = by_id[record.variant_of]
        assert (record.target_distribution, record.target_score, record.split, record.criteria) == (original.target_distribution, original.target_score, original.split, original.criteria)
        assert record.state == original.state and record.facts == original.facts
        if record.variant == "paraphrase":
            assert record.question != original.question
    reordered = [record for record in found if record.variant == "reordered"]
    assert any(list(record.facts) != list(by_id[record.variant_of].facts) for record in reordered)

    choice = by_id[f"{point.decision_id}.C"]
    assert set(choice.target_distribution) == {option.option_id for option in point.options} == {option["option_id"] for option in choice.options}
    assert sum(choice.target_distribution.values()) == pytest.approx(1.0, abs=1e-3) and choice.target_basis == "generator_policy_share"
    true, false = by_id[f"{point.decision_id}.T1"], by_id[f"{point.decision_id}.T0"]
    assert true.target_distribution == {"true": 1.0, "false": 0.0} and false.target_distribution == {"true": 0.0, "false": 1.0}
    assert false.rationale == point.distractor["unmet"] and point.distractor["label"] in false.question
    scores = [record for record in found if record.question_type == "score" and record.variant == "original"]
    assert [record.target_score for record in scores] == [option.value for option in point.options]
    assert all(str(VALUE_SAMPLES) in " ".join(record.criteria) for record in scores)


def test_turkish_runs_ask_in_turkish():
    turkish = BANKING.generate(**{**SETTINGS, "language": "tr"}, decisions=True)
    record = records(turkish.decisions[0], split="train", language="tr")[0]
    assert record.language == "tr" and "Hangi sonuç" in record.question and "Çekimser" in record.abstain["label"]
    assert all(option.label != option.event_type for option in turkish.decisions[0].options)


# Runs and export


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": ("notes.md", b"Loans are decided after checks.", "text/markdown")})
    return headers, project_id


def test_decision_scoring_and_jev_runs_record_decisions_and_others_only_when_asked(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "decisions-api@example.com")
    options = dict(target_trajectory_count=8, event_budget=None, sub_domains=["onboarding_and_kyc", "consumer_credit"])
    scored = _run(client, headers, project_id, None, consumer="decision_scoring", **options).json()
    assert scored["bundle"]["decisions"] and scored["generation"]["decisions"]["points"] == len(scored["bundle"]["decisions"])
    jev = _run(client, headers, project_id, None, target_family="jev", **options).json()
    assert jev["bundle"]["decisions"]
    trained = _run(client, headers, project_id, None, **options).json()
    assert trained["bundle"]["decisions"] == [] and trained["generation"]["decisions"] is None
    asked = _run(client, headers, project_id, None, decisions=True, **options).json()
    assert asked["bundle"]["decisions"]
    # Recording decisions draws the same journeys.
    assert [item["event_ids"] for item in asked["bundle"]["trajectories"]] == [item["event_ids"] for item in trained["bundle"]["trajectories"]]
    point = scored["bundle"]["decisions"][0]
    journey = client.get(f"/runs/{scored['id']}/journeys/{point['trajectory_id']}", headers=headers).json()
    assert journey["decisions"][0]["decision_id"] == point["decision_id"]


def test_export_writes_decision_records_that_validate_prefixes_and_the_schema(client, tmp_path, monkeypatch):
    validator = _schema_validator()
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "decisions-export@example.com")
    run = _run(client, headers, project_id, None, consumer="decision_scoring", target_trajectory_count=10, event_budget=None, group_size=2,
               sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    base = f"/runs/{run['id']}/export"
    manifest = client.get(f"{base}/manifest.json", headers=headers, params=OPEN).json()
    schema = client.get(f"{base}/decision-record.schema.json", headers=headers, params=OPEN).json()
    assert schema["title"] == f"Decision record ({DECISION_SCHEMA_VERSION})"
    text = client.get(f"{base}/decisions.jsonl", headers=headers, params=OPEN).text
    assert hashlib.sha256(text.encode()).hexdigest() == manifest["files"]["decisions.jsonl"]
    lines = [json.loads(line) for line in text.splitlines()]
    assert len(lines) == manifest["counts"]["decision_records"] == run["generation"]["decisions"]["records"]
    for line in lines:
        validator.validate(line)
        DecisionRecord.model_validate(line)
    samples = {json.loads(line)["sample_id"]: json.loads(line)["split"] for line in client.get(f"{base}/samples.jsonl", headers=headers, params=OPEN).text.splitlines()}
    mapping = {"train": "train", "validation": "calibration", "test": "held_out", "heldout": "held_out"}
    assert all(line["split"] == mapping[samples[line["sample_id"]]] for line in lines)
    assert manifest["decisions"]["schema_version"] == DECISION_SCHEMA_VERSION and manifest["decisions"]["points"] == len(run["bundle"]["decisions"])
    assert sum(manifest["decisions"]["split_counts"].values()) == len(lines)
    assert manifest["parts_for_this_run"] == ["decisions.jsonl", "decision-record.schema.json", "prefixes.jsonl"]

    prefixes = [json.loads(line) for line in client.get(f"{base}/prefixes.jsonl", headers=headers, params=OPEN).text.splitlines()]
    turns = sum(1 for sample in run["bundle"]["samples"] for sequence in sample["sequences"] for context in sequence["contexts"] for segment in context["segments"] if segment["trainable"])
    assert len(prefixes) == turns == manifest["counts"]["prefixes"]
    first = prefixes[0]
    assert first["prefix"][0]["role"] == "system" and first["prefix"][-1]["role"] == "user" and first["target"]["role"] == "assistant"
    assert first["split"] == samples[first["sample_id"]]


def test_a_large_run_records_decisions_in_every_batch_and_exports_them(client, tmp_path, monkeypatch):
    import app.generation as generation
    from app import store

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    headers, project_id = _study(client, "decisions-large@example.com")
    run = _run(client, headers, project_id, None, consumer="decision_scoring", target_trajectory_count=90, event_budget=None,
               sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    summary = run["generation"]["decisions"]
    assert run["generation"]["storage"]["batches"] > 1 and summary["points"] > 90
    assert sum(summary["groups"].values()) == summary["points"] and 0 < summary["mean_taken_value"] < 1
    assert client.post(f"/runs/{run['id']}/exports", headers=headers, json={"allow_unaccepted": True}).status_code == 200
    lines = gzip.decompress(client.get(f"/runs/{run['id']}/export/decisions.jsonl", headers=headers, params=OPEN).content).decode().splitlines()
    assert len(lines) == summary["records"]
