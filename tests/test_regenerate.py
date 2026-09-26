import json

import pytest

from app import jobs, runtime
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run

LOW = {"helpfulness": 1, "correctness": 0, "safety": 1, "pairwise_quality": 1}


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id, _ready_key(client, headers)


def _judged_run(client, headers, project_id, credential_id, scores=None, **overrides):
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=8, event_budget=None, **overrides).json()
    runtime.judge = RecordingJudge(scores=scores)
    body = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()
    return body


def test_a_rejected_run_regenerates_from_its_notes_and_the_child_is_judged(client):
    headers, project_id, credential_id = _study(client, "regenerate@example.com")
    parent = _judged_run(client, headers, project_id, credential_id, scores=LOW)
    assert parent["cycles"][0]["accepted"] is False
    notes = parent["cycles"][0]["revision_notes"]
    assert any(note.startswith("correctness") for note in notes) and any(note.startswith("helpfulness") for note in notes)
    first = client.get(f"/runs/{parent['id']}/journeys", headers=headers).json()["data"][0]["trajectory_id"]
    event_id = client.get(f"/runs/{parent['id']}/journeys/{first}", headers=headers).json()["events"][0]["event_id"]
    note = client.post(f"/runs/{parent['id']}/feedback", headers=headers, json={"target_type": "event", "target_id": event_id, "stance": "revise", "comment": "Say why."}).json()

    child = client.post(f"/runs/{parent['id']}/regenerate", headers=headers, json={})
    assert child.status_code == 200, child.text
    child = child.json()
    assert child["parent_run_id"] == parent["id"]
    assert child["config"]["regeneration"] == {"from_run": parent["id"], "round": 2, "revision_notes": notes}
    assert child["inherited_feedback_ids"] == [note["id"]]
    assert child["status"] == "evaluated" and len(child["cycles"]) == 1
    assert child["judge_job"]["status"] == "succeeded"

    applied = child["generation"]["notes"]
    effects = " ".join(item["effect"] for item in applied["revisions"])
    assert "Leaves out loan.delinquent." in effects and "Raises the minimum length by one event." in effects
    assert applied["feedback"][0]["effect"].startswith("Rewrites the sample text at ")


def test_regeneration_needs_a_judged_unaccepted_run_within_the_limit(client):
    headers, project_id, credential_id = _study(client, "regenerate-rules@example.com")
    fresh = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    refused = client.post(f"/runs/{fresh['id']}/regenerate", headers=headers, json={})
    assert refused.status_code == 409 and "Ask the judge first" in refused.json()["detail"]

    accepted = _judged_run(client, headers, project_id, credential_id)
    assert accepted["cycles"][0]["accepted"] is True
    assert client.post(f"/runs/{accepted['id']}/regenerate", headers=headers, json={}).status_code == 409

    parent = _judged_run(client, headers, project_id, credential_id, scores=LOW, max_cycles=2)
    child = client.post(f"/runs/{parent['id']}/regenerate", headers=headers, json={}).json()
    assert child["cycles"][0]["accepted"] is False
    limit = client.post(f"/runs/{child['id']}/regenerate", headers=headers, json={})
    assert limit.status_code == 409 and "judged 2 times, its limit" in limit.json()["detail"]
    assert client.post(f"/runs/{parent['id']}/regenerate", headers=headers, json={"feedback_ids": ["nope"]}).status_code == 422


class HalfReadable:
    def run_eval(self, *, rubric, judge_model=None, **kwargs):
        readable = rubric == "safety"
        return {"score": 1 if readable else 0, "parsed": {}, "raw": "{}" if readable else "", "readable": readable, "judge_model": judge_model, "duration_ms": 1}


def test_a_judged_run_is_not_judged_again_unless_the_judge_could_not_read_it(client):
    headers, project_id, credential_id = _study(client, "judge-once@example.com")
    judged = _judged_run(client, headers, project_id, credential_id, scores=LOW, max_cycles=3)
    again = client.post(f"/runs/{judged['id']}/evaluate", headers=headers, json={})
    assert again.status_code == 409 and "Regenerate from its notes" in again.json()["detail"]

    unread = _run(client, headers, project_id, credential_id, target_trajectory_count=4, max_cycles=3).json()
    runtime.judge = HalfReadable()
    first = client.post(f"/runs/{unread['id']}/evaluate", headers=headers, json={}).json()
    assert first["cycles"][0]["accepted"] is False
    runtime.judge = RecordingJudge()
    second = client.post(f"/runs/{unread['id']}/evaluate", headers=headers, json={})
    assert second.status_code == 200 and second.json()["cycles"][-1]["accepted"] is True


def test_in_worker_mode_the_child_is_judged_after_it_is_generated(client, monkeypatch):
    headers, project_id, credential_id = _study(client, "regenerate-worker@example.com")
    parent = _judged_run(client, headers, project_id, credential_id, scores=LOW)
    monkeypatch.setenv("JOBS_MODE", "worker")
    child = client.post(f"/runs/{parent['id']}/regenerate", headers=headers, json={}).json()
    assert child["status"] == "queued" and child["judge_job"] is None
    assert jobs.Worker().run_next() is True
    generated = client.get(f"/runs/{child['id']}", headers=headers).json()
    assert generated["status"] == "generated" and generated["judge_job"]["status"] == "queued"
    assert jobs.Worker().run_next() is True
    assert client.get(f"/runs/{child['id']}", headers=headers).json()["status"] == "evaluated"
    assert jobs.Worker().run_next() is False


def test_export_follows_the_judge_unless_asked_otherwise(client):
    headers, project_id, credential_id = _study(client, "export-gate@example.com")
    unjudged = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    refused = client.get(f"/runs/{unjudged['id']}/export/manifest.json", headers=headers)
    assert refused.status_code == 409 and "has not judged this run yet" in refused.json()["detail"]
    manifest = client.get(f"/runs/{unjudged['id']}/export/manifest.json", headers=headers, params={"allow_unaccepted": "true"}).json()
    assert manifest["review"] == {"accepted": False, "cycle": None, "exported_without_acceptance": True}
    assert any("had not accepted" in item for item in manifest["data_card"]["known_limitations"])

    rejected = _judged_run(client, headers, project_id, credential_id, scores=LOW)
    assert "did not accept" in client.get(f"/runs/{rejected['id']}/export/samples.jsonl", headers=headers).json()["detail"]

    accepted = _judged_run(client, headers, project_id, credential_id)
    manifest = client.get(f"/runs/{accepted['id']}/export/manifest.json", headers=headers).json()
    assert manifest["review"] == {"accepted": True, "cycle": 1, "exported_without_acceptance": False}
    assert manifest["judge_cycles"][0]["models"]


def test_a_large_accepted_run_exports_without_the_override_and_apart_from_an_unaccepted_one(client, monkeypatch, tmp_path):
    from app import store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    store._read_json.cache_clear()
    headers, project_id, credential_id = _study(client, "export-gate-large@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=80, event_budget=None).json()
    assert client.post(f"/runs/{run['id']}/exports", headers=headers, json={}).status_code == 409
    assert client.post(f"/runs/{run['id']}/exports", headers=headers, json={"allow_unaccepted": True}).status_code == 200
    runtime.judge = RecordingJudge()
    client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    listed = client.post(f"/runs/{run['id']}/exports", headers=headers, json={}).json()["data"]
    assert sorted((item["unaccepted"], item["ready"]) for item in listed) == [(False, True), (True, True)]
    manifest = client.get(f"/runs/{run['id']}/export/manifest.json", headers=headers).json()
    assert manifest["review"]["accepted"] is True
    assert (tmp_path / "runs" / run["id"] / "exports" / "all-unaccepted" / "manifest.json").is_file()


def test_the_diff_shows_what_the_notes_changed(client):
    headers, project_id, credential_id = _study(client, "diff@example.com")
    parent = _judged_run(client, headers, project_id, credential_id, scores=LOW, sub_domains=["onboarding_and_kyc", "consumer_credit"])
    runtime.judge = RecordingJudge()
    child = client.post(f"/runs/{parent['id']}/regenerate", headers=headers, json={}).json()
    diff = client.get(f"/runs/{child['id']}/diff", headers=headers)
    assert diff.status_code == 200, diff.text
    diff = diff.json()
    assert diff["parent_run_id"] == parent["id"] and diff["regeneration"]["round"] == 2
    assert diff["config"] == []
    assert diff["notes"]["revisions"] and all(item["effect"] for item in diff["notes"]["revisions"])
    names = {item["name"] for item in diff["metrics"]}
    assert {"Journeys", "Events", "Distinct variants", "Rule violations"} <= names
    assert all(set(item) == {"type", "before", "after"} for item in diff["event_shifts"])
    assert diff["scores"]["before"]["accepted"] is False and diff["scores"]["after"]["accepted"] is True
    assert diff["scores"]["after"]["headline"] > diff["scores"]["before"]["headline"]
    assert not any(item["type"] == "loan.delinquent" and item["after"] > 0 for item in diff["event_shifts"])

    rerun = client.post(f"/runs/{child['id']}/rerun", headers=headers, json={"feedback_ids": [], "language": "tr"}).json()
    changed = client.get(f"/runs/{rerun['id']}/diff", headers=headers).json()["config"]
    assert {"key": "language", "before": "en", "after": "tr"} in changed
    assert client.get(f"/runs/{parent['id']}/diff", headers=headers).status_code == 404
    assert "credential" not in json.dumps(changed)
