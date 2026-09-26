import gzip
import hashlib
import json

import pytest

from app import store
from test_api import _auth, _link, _project, _ready_key, _run


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    import app.generation as generation

    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()


def _setup(client, email, **overrides):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    payload = {"target_trajectory_count": 150, "event_budget": None, "min_events": 6, "max_events": 16}
    payload.update(overrides)
    created = _run(client, headers, project_id, credential_id, **payload)
    return headers, created


def test_a_large_run_exports_through_a_job_into_gzip_parts(client):
    headers, created = _setup(client, "export-job@example.com", sub_domains=["onboarding_and_kyc", "deposits", "consumer_credit"])
    run = created.json()
    assert client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers).status_code == 409

    prepared = client.post(f"/runs/{run['id']}/exports", headers=headers, json={})
    assert prepared.status_code == 200, prepared.text
    listed = client.get(f"/runs/{run['id']}/exports", headers=headers).json()["data"]
    assert listed[0]["ready"] is True and listed[0]["job"]["status"] == "succeeded"
    assert listed[0]["sizes"]["samples.jsonl"] > listed[0]["download_sizes"]["samples.jsonl"] > 0

    manifest = client.get(f"/runs/{run['id']}/export/manifest.json", headers=headers).json()
    samples = client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers)
    assert samples.headers["content-type"] == "application/gzip"
    assert "samples.jsonl.gz" in samples.headers["content-disposition"]
    text = gzip.decompress(samples.content).decode()
    assert hashlib.sha256(text.encode()).hexdigest() == manifest["files"]["samples.jsonl"]
    lines = [json.loads(line) for line in text.splitlines()]
    assert len(lines) == manifest["counts"]["samples"] == 300
    assert {line["split"] for line in lines} <= {"train", "validation", "test"}

    ocel = json.loads(gzip.decompress(client.get(f"/runs/{run['id']}/export/ocel.json", headers=headers).content))
    object_ids = {obj["id"] for obj in ocel["objects"]}
    assert len(ocel["events"]) == run["generation"]["event_count"]
    assert all(rel["objectId"] in object_ids for event in ocel["events"] for rel in event["relationships"])

    domain = gzip.decompress(client.get(f"/runs/{run['id']}/export/domain.jsonl", headers=headers).content).decode()
    assert sum(1 for line in domain.splitlines() if '"record_type":"event"' in line) == run["generation"]["event_count"]

    held = client.post(f"/runs/{run['id']}/exports", headers=headers, json={"held_out": "consumer_credit"})
    assert held.status_code == 200
    held_manifest = client.get(f"/runs/{run['id']}/export/manifest.json", headers=headers, params={"held_out": "consumer_credit"}).json()
    assert held_manifest["split"]["counts"]["heldout"] > 0
    assert len(client.get(f"/runs/{run['id']}/exports", headers=headers).json()["data"]) == 2


def test_small_runs_export_directly_not_through_a_job(client):
    headers, created = _setup(client, "small-export@example.com", target_trajectory_count=8)
    run = created.json()
    assert client.post(f"/runs/{run['id']}/exports", headers=headers, json={}).status_code == 409
    assert client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers).status_code == 200


def test_an_accepted_target_draws_until_enough_groups_are_accepted(client):
    headers, created = _setup(client, "accepted@example.com", target_trajectory_count=10, group_size=4, target_kind="accepted_groups",
                              sub_domains=["onboarding_and_kyc", "deposits", "consumer_credit"])
    assert created.status_code == 200, created.text
    generation = created.json()["generation"]
    target = generation["target"]
    assert target["kind"] == "accepted_groups"
    assert target["reached"] >= 10
    assert generation["rewards"]["accepted_groups"] == target["reached"]
    assert target["buckets"][0]["generated"] > target["buckets"][0]["accepted"]
    assert generation["limited_by"] is None


def test_an_unreachable_accepted_target_stops_at_the_ceiling_and_says_so(client):
    # A one-event journey holds no decision, so no group can mix a pass and a fail.
    headers, created = _setup(client, "ceiling@example.com", target_trajectory_count=3, group_size=2, target_kind="accepted_groups",
                              sub_domains=["complaints"], min_events=1, max_events=1)
    assert created.status_code == 200, created.text
    generation = created.json()["generation"]
    assert generation["limited_by"] == "acceptance"
    assert generation["target"]["reached"] < 3
    assert generation["target"]["buckets"][0]["generated"] == 15
    assert generation["target"]["buckets"][0]["stopped_by"] == "acceptance"


def test_shares_split_the_run_into_buckets_with_their_own_targets(client):
    headers, created = _setup(client, "shares@example.com", target_trajectory_count=40, sub_domains=["deposits", "consumer_credit"],
                              domain_shares={"deposits": 3, "consumer_credit": 1})
    assert created.status_code == 200, created.text
    run = created.json()
    buckets = run["generation"]["target"]["buckets"]
    assert [(item["sub_domains"], item["target"], item["generated"]) for item in buckets] == [(["deposits"], 30, 30), (["consumer_credit"], 10, 10)]
    assert run["config"]["domain_shares"] == {"deposits": 0.75, "consumer_credit": 0.25}
    page = client.get(f"/runs/{run['id']}/journeys", headers=headers, params={"limit": 500}).json()
    assert page["total"] == 40


def test_targets_and_shares_are_validated(client):
    headers, created = _setup(client, "validate@example.com", target_kind="accepted_groups", group_size=1)
    assert created.status_code == 422 and "group size above 1" in created.json()["detail"]
    headers, created = _setup(client, "validate2@example.com", domain_shares={"complaints": 1})
    assert created.status_code == 422
    headers, created = _setup(client, "validate3@example.com", domain_shares={"deposits": 0, "onboarding_and_kyc": 1})
    assert created.status_code == 422


def test_a_crashed_bucketed_run_resumes_and_matches_an_uninterrupted_one(client, monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")
    options = dict(target_trajectory_count=24, group_size=4, target_kind="accepted_groups",
                   sub_domains=["onboarding_and_kyc", "risk_and_compliance"], domain_shares={"onboarding_and_kyc": 1, "risk_and_compliance": 1})
    headers, interrupted = _setup(client, "bucket-crash@example.com", **options)
    reference_headers, reference = _setup(client, "bucket-reference@example.com", **options)
    interrupted, reference = interrupted.json(), reference.json()
    import app.generation as generation
    from app import jobs
    from app.db import SessionLocal
    from app.models import Job

    real = generation.write_json
    calls = {"batches": 0}

    def crash_after_three(path, value):
        real(path, value)
        if path.name.endswith(".json.gz"):
            calls["batches"] += 1
            if calls["batches"] == 3:
                raise SystemExit("worker killed")

    monkeypatch.setattr(generation, "write_json", crash_after_three)
    with pytest.raises(SystemExit):
        jobs.run_job(interrupted["job"]["id"])
    monkeypatch.setattr(generation, "write_json", real)
    db = SessionLocal()
    job = db.get(Job, interrupted["job"]["id"])
    job.status = "queued"
    db.commit()
    db.close()
    while jobs.Worker().run_next():
        pass
    ours = client.get(f"/runs/{interrupted['id']}", headers=headers).json()
    theirs = client.get(f"/runs/{reference['id']}", headers=reference_headers).json()
    assert ours["status"] == theirs["status"] == "generated"
    assert ours["generation"]["target"] == theirs["generation"]["target"]
    assert ours["generation"]["limited_by"] is None and ours["generation"]["target"]["reached"] >= 24
    journeys = lambda run_id, h: [(row["trajectory_id"], row["variant"]) for row in
                                  client.get(f"/runs/{run_id}/journeys", headers=h, params={"limit": 1000}).json()["data"]]
    assert journeys(interrupted["id"], headers) == journeys(reference["id"], reference_headers)


def test_an_export_is_prepared_once_and_leaves_the_run_job_alone(client, monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")
    headers, created = _setup(client, "export-once@example.com")
    run = created.json()
    from app import jobs

    assert jobs.Worker().run_next() is True
    generate_job = client.get(f"/runs/{run['id']}", headers=headers).json()["job"]
    first = client.post(f"/runs/{run['id']}/exports", headers=headers, json={}).json()["data"]
    again = client.post(f"/runs/{run['id']}/exports", headers=headers, json={}).json()["data"]
    assert first[0]["job"]["id"] == again[0]["job"]["id"] and again[0]["job"]["status"] == "queued"
    assert client.get(f"/runs/{run['id']}", headers=headers).json()["job"] == generate_job
    assert client.post(f"/runs/{run['id']}/cancel", headers=headers).status_code == 409
    assert jobs.Worker().run_next() is True and jobs.Worker().run_next() is False
    done = client.post(f"/runs/{run['id']}/exports", headers=headers, json={}).json()["data"]
    assert done[0]["ready"] is True and done[0]["job"]["id"] == first[0]["job"]["id"]
