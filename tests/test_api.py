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
    assert created.json()["bundle_source"] == "candidate"
    assert created.json()["status"] == "generated"
    assert created.json()["generation_active"] is True
    assert created.json()["generation"]["generator_id"] == "banking-semi-markov-v1"


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
    event_id = created.json()["bundle"]["events"][0]["event_id"]
    note = client.post(
        f"/runs/{run_id}/feedback",
        headers=headers,
        json={"target_type": "event", "target_id": event_id, "stance": "revise", "comment": "Activation felt early."},
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


def test_rerun_applies_drop_and_revise_notes(client):
    headers = _auth(client, "regen@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    created = _run(
        client,
        headers,
        project_id,
        credential_id,
        sub_domains=["onboarding_and_kyc", "cards_and_payments"],
        target_trajectory_count=4,
        min_events=6,
        max_events=16,
        event_budget=400,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    purchase = next(event["event_id"] for event in body["bundle"]["events"] if event["event_type"] == "card.purchase_authorised")
    activation = next(event["event_id"] for event in body["bundle"]["events"] if event["event_type"] == "card.activated")
    dropped = client.post(
        f"/runs/{body['id']}/feedback",
        headers=headers,
        json={"target_type": "event", "target_id": purchase, "stance": "drop", "comment": "Leave the purchase out."},
    )
    revised = client.post(
        f"/runs/{body['id']}/feedback",
        headers=headers,
        json={"target_type": "event", "target_id": activation, "stance": "revise", "comment": "Wait a week before activation."},
    )
    assert dropped.status_code == 200, dropped.text
    assert revised.status_code == 200, revised.text
    child = client.post(
        f"/runs/{body['id']}/rerun",
        headers=headers,
        json={"feedback_ids": [dropped.json()["id"], revised.json()["id"]]},
    )
    assert child.status_code == 200, child.text
    payload = child.json()
    types = {event["event_type"] for event in payload["bundle"]["events"]}
    assert "card.purchase_authorised" not in types
    assert "card.activated" in types
    rendered = json.dumps(payload["bundle"]["samples"])
    assert "Wait a week before activation." in rendered
    events = {event["event_id"]: event for event in payload["bundle"]["events"]}
    for trajectory in payload["bundle"]["trajectories"]:
        if trajectory["parent_trajectory_id"]:
            continue
        issued = next((events[item] for item in trajectory["event_ids"] if events[item]["event_type"] == "card.issued"), None)
        activated = next((events[item] for item in trajectory["event_ids"] if events[item]["event_type"] == "card.activated"), None)
        if issued and activated:
            from datetime import datetime

            gap = datetime.fromisoformat(activated["event_time"]) - datetime.fromisoformat(issued["event_time"])
            assert gap.total_seconds() >= 7 * 24 * 3600


def test_warm_corpus_steers_generation_and_scrubs_identifiers(client):
    headers = _auth(client, "steer@example.com", "password-123")
    project_id = _project(client, headers)
    upload = client.post(
        f"/projects/{project_id}/corpus",
        headers=headers,
        data={"kind": "paper"},
        files={"upload": ("notes.txt", b"Customers fund savings accounts in USD via mobile. Contact ada@example.com account 1234567890123456 sk-supersecretkey", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    credential_id = _ready_key(client, headers)
    created = _run(
        client,
        headers,
        project_id,
        credential_id,
        sub_domains=["deposits", "onboarding_and_kyc"],
        target_trajectory_count=2,
        event_budget=200,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    dumped = json.dumps(body)
    assert "ada@example.com" not in dumped
    assert "1234567890123456" not in dumped
    assert "sk-supersecretkey" not in dumped
    assert any(event.get("currency") == "USD" for event in body["bundle"]["events"])
    assert any(obj.get("subtype") == "savings" for obj in body["bundle"]["objects"])
    assert any(event["event_type"] == "product.viewed" and event["channel_id"] == "mobile" for event in body["bundle"]["events"])


def test_event_budget_limits_how_many_journeys_are_stored(client):
    headers = _auth(client, "budget@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    created = _run(
        client,
        headers,
        project_id,
        credential_id,
        target_trajectory_count=20,
        min_events=8,
        max_events=10,
        event_budget=15,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["generation"]["primary_trajectories"] < 20
    assert body["generation"]["limited_by"] == "event_budget"
    assert body["generation"]["event_count"] <= 15
    assert body["generation"]["event_count"] == len(body["bundle"]["events"])


def test_deep_search_stores_a_scrubbed_report_and_hides_the_key(client):
    headers = _auth(client, "search@example.com", "password-123")
    project_id = _project(client, headers)
    secret = "sk-search-user-key-0001"
    credential_id = _ready_key(client, headers, secret=secret)

    class RecordingSearcher:
        def __init__(self):
            self.keys = []
            self.queries = []

        def search(self, query, *, provider, key):
            from app.search import DeepSearchResult

            self.keys.append(key)
            self.queries.append(query)
            return DeepSearchResult(
                text=(
                    "Customers fund savings accounts in USD via mobile. "
                    "card.issued comes before card.activated. "
                    f"Contact ada@example.com {secret} 1234567890123456"
                ),
                sources=["https://example.test/savings", "not a url"],
                model="search-model",
            )

    runtime.searcher = RecordingSearcher()
    response = client.post(
        f"/projects/{project_id}/deep-search",
        headers=headers,
        json={"credential_id": credential_id, "sub_domains": ["deposits", "cards_and_payments"], "language": "en"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    dumped = json.dumps(body)
    assert secret not in dumped
    assert "ada@example.com" not in dumped
    assert "1234567890123456" not in dumped
    assert body["kind"] == "deep_search"
    assert body["provider"] == "openai"
    assert body["sources"] == ["https://example.test/savings"]
    assert "USD" in body["excerpt"]
    assert "deposits" in runtime.searcher.queries[0]
    assert runtime.searcher.keys == [secret]

    created = _run(
        client,
        headers,
        project_id,
        credential_id,
        sub_domains=["deposits", "cards_and_payments", "onboarding_and_kyc"],
        target_trajectory_count=2,
        min_events=6,
        max_events=20,
        event_budget=200,
    )
    assert created.status_code == 200, created.text
    run = created.json()
    assert secret not in json.dumps(run)
    assert any(event.get("currency") == "USD" for event in run["bundle"]["events"])
    assert any(event["event_type"] == "product.viewed" and event["channel_id"] == "mobile" for event in run["bundle"]["events"])
    assert "card.issued" in json.dumps(run["bundle"]["samples"])


def test_user_cannot_deep_search_with_a_platform_key_and_failures_hide_the_secret(client):
    from app.search import DeepSearchError

    user = _auth(client, "nosearch@example.com", "password-123")
    admin = _admin(client)
    project_id = _project(client, user)
    platform = client.post(
        "/credentials",
        headers=admin,
        json={"provider": "openai", "label": "house", "secret": "sk-platform-search-key", "scope": "platform"},
    )
    assert platform.status_code == 200, platform.text
    denied = client.post(
        f"/projects/{project_id}/deep-search",
        headers=user,
        json={"credential_id": platform.json()["id"], "sub_domains": ["deposits"], "language": "en"},
    )
    assert denied.status_code in {403, 404}
    assert "sk-platform-search-key" not in denied.text

    own = _ready_key(client, user, secret="sk-user-search-key-001")

    class FailingSearcher:
        def search(self, query, *, provider, key):
            raise DeepSearchError(provider, 401, f"rejected {key}")

    runtime.searcher = FailingSearcher()
    failed = client.post(
        f"/projects/{project_id}/deep-search",
        headers=user,
        json={"credential_id": own, "sub_domains": ["deposits"], "language": "en"},
    )
    assert failed.status_code == 502, failed.text
    assert "sk-user-search-key-001" not in failed.text
    assert "OpenAI deep search failed" in failed.json()["detail"]


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
