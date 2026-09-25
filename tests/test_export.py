import hashlib
import json

from test_api import _auth, _link, _project, _ready_key, _run


def _setup(client, email, **overrides):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    payload = {"sub_domains": ["onboarding_and_kyc", "deposits", "consumer_credit"], "target_trajectory_count": 8, "event_budget": None, "group_size": 4}
    payload.update(overrides)
    created = _run(client, headers, project_id, credential_id, **payload)
    assert created.status_code == 200, created.text
    return headers, created.json()


def _part(client, headers, run_id, part, **params):
    response = client.get(f"/runs/{run_id}/export/{part}", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response


def test_export_parts_are_consistent_and_reproducible(client):
    headers, run = _setup(client, "export@example.com")
    samples = _part(client, headers, run["id"], "samples.jsonl")
    assert samples.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in samples.headers["content-disposition"]
    lines = [json.loads(line) for line in samples.text.splitlines()]
    assert len(lines) == len(run["bundle"]["samples"])
    assert {line["split"] for line in lines} <= {"train", "validation", "test"}
    assert all(len(line["sequences"]) == 4 for line in lines)
    assert _part(client, headers, run["id"], "samples.jsonl").text == samples.text

    domain = [json.loads(line) for line in _part(client, headers, run["id"], "domain.jsonl").text.splitlines()]
    kinds = {line["record_type"] for line in domain}
    assert kinds == {"object", "relationship", "event", "event_object", "state_transition", "trajectory"}
    assert sum(1 for line in domain if line["record_type"] == "event") == len(run["bundle"]["events"])

    ocel = json.loads(_part(client, headers, run["id"], "ocel.json").text)
    assert set(ocel) == {"objectTypes", "eventTypes", "objects", "events"}
    object_ids = {obj["id"] for obj in ocel["objects"]}
    assert all(rel["objectId"] in object_ids for event in ocel["events"] for rel in event["relationships"])
    funded = {event["id"] for event in ocel["events"] if event["type"] == "account.funded"}
    assert funded
    assert all(
        any(rel["qualifier"] == "credited_account" for rel in event["relationships"])
        for event in ocel["events"]
        if event["id"] in funded
    )
    assert any(attr["name"] == "application" for obj in ocel["objects"] for attr in obj["attributes"])

    manifest = json.loads(_part(client, headers, run["id"], "manifest.json").text)
    assert manifest["group_size"] == 4
    assert manifest["counts"]["samples"] == len(lines)
    assert sum(manifest["split"]["counts"].values()) == len(lines)
    assert manifest["files"]["samples.jsonl"] == hashlib.sha256(samples.text.encode()).hexdigest()
    assert "credential_id" not in manifest["configuration"]
    assert manifest["data_card"]["known_limitations"]
    assert "synthetic" in manifest["synthetic"].lower()
    text = json.dumps(manifest)
    assert "sk-test-user-key" not in text and "fingerprint" not in text


def test_a_held_out_sub_domain_moves_its_samples_out_of_the_splits(client):
    headers, run = _setup(client, "heldout@example.com", target_trajectory_count=12)
    lines = [json.loads(line) for line in _part(client, headers, run["id"], "samples.jsonl", held_out="consumer_credit").text.splitlines()]
    events = {event["event_id"]: event["event_type"] for event in run["bundle"]["events"]}
    trajectories = {item["trajectory_id"]: item for item in run["bundle"]["trajectories"]}
    for line in lines:
        reached = {events[e] for sequence in line["sequences"] for e in trajectories[sequence["trajectory_id"]]["event_ids"]}
        assert (line["split"] == "heldout") == ("loan.disbursed" in reached)
    assert any(line["split"] == "heldout" for line in lines)
    manifest = json.loads(_part(client, headers, run["id"], "manifest.json", held_out="consumer_credit").text)
    assert manifest["split"]["held_out_sub_domain"] == "consumer_credit"
    assert manifest["split"]["counts"]["heldout"] == sum(1 for line in lines if line["split"] == "heldout")


def test_export_is_owner_only_and_validates_its_inputs(client):
    headers, run = _setup(client, "owner-export@example.com")
    other = _auth(client, "stranger@example.com", "password-123")
    assert client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=other).status_code == 404
    assert client.get(f"/runs/{run['id']}/export/secrets.txt", headers=headers).status_code == 404
    assert client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers, params={"held_out": "complaints"}).status_code == 422
