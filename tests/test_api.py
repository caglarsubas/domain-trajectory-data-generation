import json

import httpx

from app.judge import InferenceEngineClient
from app import runtime
from trajectory_contract import banking_fixture


class RecordingJudge:
    def __init__(self, scores: dict[str, float] | None = None, fail_if_called: bool = False):
        self.calls: list[dict] = []
        self.scores = scores or {
            "helpfulness": 5,
            "correctness": 1,
            "safety": 1,
            "pairwise_quality": 1,
        }
        self.fail_if_called = fail_if_called

    def run_eval(self, **kwargs):
        if self.fail_if_called:
            raise AssertionError("judge should not be called")
        self.calls.append(kwargs)
        rubric = kwargs["rubric"]
        return {
            "score": self.scores[rubric],
            "parsed": {"justification": f"{rubric} ok"},
            "raw": "{}",
            "judge_model": "fake-judge",
            "duration_ms": 1.5,
        }


def _auth(client, email, password, kind="user"):
    response = client.post("/auth/register", json={"email": email, "password": password, "kind": kind})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _admin(client):
    response = client.post("/auth/login", json={"email": "admin@example.com", "password": "admin-pass-123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _ready_key(client, headers, provider="openai", secret="sk-test-user-key-0001", scope="byok"):
    response = client.post(
        "/credentials",
        headers=headers,
        json={"provider": provider, "label": "primary", "secret": secret, "scope": scope},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "secret" not in body
    assert body["fingerprint"]
    assert body["ready"] is True
    return body["id"]


def _project(client, headers, name="Retail onboarding"):
    response = client.post("/projects", headers=headers, json={"name": name, "sector": "banking"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _link(client, headers, project_id):
    response = client.post(
        f"/projects/{project_id}/corpus/link",
        headers=headers,
        json={"kind": "paper", "name": "BIAN notes", "uri": "https://example.test/bian"},
    )
    assert response.status_code == 200, response.text


def _run(client, headers, project_id, credential_id, **overrides):
    payload = {
        "project_id": project_id,
        "sector": "banking",
        "target_trajectory_count": 40,
        "event_budget": 800,
        "min_events": 8,
        "max_events": 24,
        "max_assistant_turns": 12,
        "sub_domains": ["onboarding_and_kyc", "deposits"],
        "language": "en",
        "start_mode": "warm",
        "cold_start_acknowledged": False,
        "reward_mechanism": "binary_outcome",
        "signal_mechanism": "outcome",
        "consumer": "post_training",
        "target_family": "llm",
        "max_cycles": 2,
        "credential_id": credential_id,
    }
    payload.update(overrides)
    response = client.post("/runs", headers=headers, json=payload)
    return response


def test_user_cannot_store_platform_key_and_demo_needs_byok(client):
    user = _auth(client, "user@example.com", "password-123")
    demo = _auth(client, "demo@example.com", "password-123", kind="demo")
    admin = _admin(client)
    denied = client.post(
        "/credentials",
        headers=user,
        json={"provider": "openai", "label": "platform", "secret": "sk-should-not-save", "scope": "platform"},
    )
    assert denied.status_code == 403
    platform = client.post(
        "/credentials",
        headers=admin,
        json={"provider": "anthropic", "label": "house", "secret": "sk-ant-admin-key-1234", "scope": "platform"},
    )
    assert platform.status_code == 200
    assert platform.json()["scope"] == "platform"

    project_id = _project(client, demo)
    _link(client, demo, project_id)
    own = _ready_key(client, demo, secret="sk-demo-own-key-00001")
    created = _run(client, demo, project_id, own)
    assert created.status_code == 200, created.text
    assert created.json()["bundle_source"] == "fixture"
    assert created.json()["generation_active"] is False


def test_warm_start_and_cold_start_rules(client):
    headers = _auth(client, "warm@example.com", "password-123")
    project_id = _project(client, headers)
    credential_id = _ready_key(client, headers)
    missing = _run(client, headers, project_id, credential_id)
    assert missing.status_code == 422
    assert "warm start" in missing.json()["detail"]
    cold = _run(client, headers, project_id, credential_id, start_mode="cold", cold_start_acknowledged=False)
    assert cold.status_code == 422
    assert "acknowledgment" in cold.json()["detail"]
    acknowledged = _run(
        client,
        headers,
        project_id,
        credential_id,
        start_mode="cold",
        cold_start_acknowledged=True,
    )
    assert acknowledged.status_code == 200
    _link(client, headers, project_id)
    warm = _run(client, headers, project_id, credential_id)
    assert warm.status_code == 200


def test_non_banking_sector_is_rejected(client):
    headers = _auth(client, "sector@example.com", "password-123")
    response = client.post("/projects", headers=headers, json={"name": "Later", "sector": "insurance"})
    assert response.status_code == 422
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    run = _run(client, headers, project_id, credential_id, sector="airways")
    assert run.status_code == 422


def test_failed_lifecycle_does_not_call_the_judge(client):
    headers = _auth(client, "check@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    created = _run(client, headers, project_id, credential_id)
    run_id = created.json()["id"]
    judge = RecordingJudge(fail_if_called=True)
    runtime.judge = judge
    bundle = banking_fixture()
    bundle.events[8].event_time = bundle.events[8].event_time.replace(year=2020)
    response = client.post(
        f"/runs/{run_id}/evaluate",
        headers=headers,
        json={"candidate": bundle.model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cycles"][0]["hard_check_passed"] is False
    assert body["cycles"][0]["verdicts"] == []
    assert judge.calls == []
    assert "INFERENCE_ENGINE_API_KEY" not in json.dumps(body)


def test_eval_stores_verdicts_and_rerun_keeps_feedback(client):
    headers = _auth(client, "loop@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers, provider="xai", secret="xai-user-key-0000001")
    created = _run(client, headers, project_id, credential_id, start_mode="cold", cold_start_acknowledged=True)
    run_id = created.json()["id"]
    runtime.judge = RecordingJudge()
    evaluated = client.post(f"/runs/{run_id}/evaluate", headers=headers, json={})
    assert evaluated.status_code == 200, evaluated.text
    cycle = evaluated.json()["cycles"][0]
    assert cycle["reference_quality"] == "weak"
    assert cycle["accepted"] is True
    assert {row["rubric"] for row in cycle["verdicts"]} == {
        "helpfulness",
        "correctness",
        "safety",
        "pairwise_quality",
    }
    assert cycle["judge_key_id"] == "domain-trajectory-data-generation-primary"
    note = client.post(
        f"/runs/{run_id}/feedback",
        headers=headers,
        json={"target_type": "event", "target_id": "E09", "stance": "revise", "comment": "Activation felt early."},
    )
    assert note.status_code == 200, note.text
    child = client.post(
        f"/runs/{run_id}/rerun",
        headers=headers,
        json={"feedback_ids": [note.json()["id"]], "language": "tr", "max_events": 30},
    )
    assert child.status_code == 200, child.text
    body = child.json()
    assert body["parent_run_id"] == run_id
    assert body["inherited_feedback_ids"] == [note.json()["id"]]
    assert body["config"]["language"] == "tr"
    assert body["config"]["max_events"] == 30
    assert "INFERENCE_ENGINE_API_KEY" not in json.dumps(body)


def test_low_score_records_revision_notes(client):
    headers = _auth(client, "notes@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers, provider="google", secret="AIza-google-key-000000000")
    created = _run(client, headers, project_id, credential_id)
    runtime.judge = RecordingJudge(scores={"helpfulness": 1, "correctness": 0, "safety": 1, "pairwise_quality": 0})
    response = client.post(f"/runs/{created.json()['id']}/evaluate", headers=headers, json={})
    assert response.status_code == 200, response.text
    cycle = response.json()["cycles"][0]
    assert cycle["accepted"] is False
    assert any("helpfulness" in note for note in cycle["revision_notes"])


def test_missing_judge_configuration_returns_503(client):
    headers = _auth(client, "nojude@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    created = _run(client, headers, project_id, credential_id)
    runtime.judge = None
    response = client.post(f"/runs/{created.json()['id']}/evaluate", headers=headers, json={})
    assert response.status_code == 503
    assert "INFERENCE_ENGINE_API_KEY" in response.json()["detail"]
    assert "sk-" not in response.text


def test_deep_search_is_stubbed():
    from app.providers import get_provider

    try:
        get_provider("openai").deep_search("banking ontology", key="sk-test")
    except NotImplementedError:
        return
    raise AssertionError("deep_search should stay unimplemented")


def test_inference_client_sends_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "id": "eval_1",
                "object": "eval",
                "created": 1,
                "rubric": "safety",
                "judge_model": "engine-judge",
                "verdict": {"score": 1, "parsed": {"safe": True}, "raw": "{}", "parse_status": "clean"},
                "duration_ms": 4,
            },
        )

    client = InferenceEngineClient(
        base_url="http://engine.test",
        api_key="sk-tenant-secret",
        tenant="domain-trajectory-data-generation",
        org_id="org-trajdata",
        key_id="domain-trajectory-data-generation-primary",
        transport=httpx.MockTransport(handler),
    )
    verdict = client.run_eval(rubric="safety", prompt="p", response="r")
    assert seen["authorization"] == "Bearer sk-tenant-secret"
    assert seen["path"] == "/v1/evals/run"
    assert verdict["score"] == 1
    assert "sk-tenant-secret" not in json.dumps({k: v for k, v in verdict.items()})
    client.close()
