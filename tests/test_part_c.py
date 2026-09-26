import json

import httpx
import pytest

from app import jobs, runtime
from app.db import SessionLocal
from app.models import Credential, Job
from app.providers import KeyCheck, check_key
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run


@pytest.fixture()
def worker_mode(monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")


def _study(client, email, kind="user"):
    headers = _auth(client, email, "password-123", kind=kind)
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id, _ready_key(client, headers)


def _generated_run(client, headers, project_id, credential_id, **overrides):
    created = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None, **overrides)
    assert created.status_code == 200, created.text
    while jobs.Worker().run_next():
        pass
    return client.get(f"/runs/{created.json()['id']}", headers=headers).json()


class Searcher:
    def __init__(self):
        self.keys = []

    def search(self, query, *, provider, key):
        from app.search import DeepSearchResult

        self.keys.append(key)
        return DeepSearchResult(text="Customers open savings accounts on mobile.", sources=["https://example.test/a"], model="m")


# The judge as a job


def test_the_judge_runs_as_a_job_and_records_its_cycle(client, worker_mode):
    headers, project_id, credential_id = _study(client, "judge-job@example.com")
    run = _generated_run(client, headers, project_id, credential_id)
    runtime.judge = RecordingJudge()

    queued = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert queued.status_code == 200, queued.text
    body = queued.json()
    assert body["judge_job"]["status"] == "queued" and body["cycles"] == []
    assert body["job"]["kind"] == "generate" and body["job"]["status"] == "succeeded"
    assert client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).status_code == 409
    assert client.post(f"/runs/{run['id']}/cancel", headers=headers).status_code == 409

    assert jobs.Worker().run_next() is True
    done = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert done["status"] == "evaluated"
    assert len(done["cycles"]) == 1
    assert done["judge_job"]["status"] == "succeeded"
    assert done["judge_job"]["result"] == {"cycle_index": 1, "accepted": done["cycles"][0]["accepted"]}
    assert len(runtime.judge.calls) == len(done["cycles"][0]["verdicts"]) > 4


def test_a_queued_judge_job_can_be_cancelled_and_a_failure_is_kept_on_the_job(client, worker_mode):
    headers, project_id, credential_id = _study(client, "judge-cancel@example.com")
    run = _generated_run(client, headers, project_id, credential_id)
    queued = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["judge_job"]
    cancelled = client.post(f"/jobs/{queued['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.get(f"/runs/{run['id']}", headers=headers).json()["status"] == "generated"

    # With no engine configured the job fails with the reason the request used to return as a 503.
    client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert jobs.Worker().run_next() is True
    failed = client.get(f"/runs/{run['id']}", headers=headers).json()["judge_job"]
    assert failed["status"] == "failed" and failed["result"] == {"http_status": 503}
    assert failed["error"]


def test_jobs_belong_to_their_owner(client, worker_mode):
    headers, project_id, credential_id = _study(client, "owner-job@example.com")
    run = _generated_run(client, headers, project_id, credential_id)
    job_id = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["judge_job"]["id"]
    other = _auth(client, "other-job@example.com", "password-123")
    assert client.get(f"/jobs/{job_id}", headers=other).status_code == 404
    assert client.post(f"/jobs/{job_id}/cancel", headers=other).status_code == 404
    assert client.get(f"/jobs/{job_id}", headers=headers).json()["kind"] == "evaluate"


# Deep search as a job


def test_a_deep_search_runs_as_a_job_and_never_stores_the_key(client, worker_mode):
    secret = "sk-deep-search-job-0001"
    headers = _auth(client, "search-job@example.com", "password-123")
    project_id = _project(client, headers)
    credential_id = _ready_key(client, headers, secret=secret)
    runtime.searcher = Searcher()

    started = client.post(
        f"/projects/{project_id}/deep-search", headers=headers, json={"credential_id": credential_id, "sub_domains": ["deposits"], "language": "en"}
    )
    assert started.status_code == 200, started.text
    job = started.json()["job"]
    assert job["status"] == "queued" and job["project_id"] == project_id and "id" not in {k for k in started.json() if k != "job"}

    assert jobs.Worker().run_next() is True
    done = client.get(f"/jobs/{job['id']}", headers=headers).json()
    assert done["status"] == "succeeded"
    assert done["result"]["kind"] == "deep_search" and done["result"]["sources"] == ["https://example.test/a"]
    corpus = next(item for item in client.get("/projects", headers=headers).json()["data"] if item["id"] == project_id)["corpus"]
    assert any(item["id"] == done["result"]["id"] for item in corpus)
    assert runtime.searcher.keys == [secret]
    db = SessionLocal()
    row = db.get(Job, job["id"])
    assert secret not in json.dumps([row.payload, row.result, row.message, row.error])
    db.close()


def test_a_failed_deep_search_job_keeps_its_reason_and_hides_the_key(client, worker_mode):
    from app.search import DeepSearchError

    secret = "sk-deep-search-fail-001"
    headers = _auth(client, "search-fail@example.com", "password-123")
    project_id = _project(client, headers)
    credential_id = _ready_key(client, headers, secret=secret)

    class Failing:
        def search(self, query, *, provider, key):
            raise DeepSearchError(provider, 401, f"rejected {key}")

    runtime.searcher = Failing()
    job = client.post(
        f"/projects/{project_id}/deep-search", headers=headers, json={"credential_id": credential_id, "sub_domains": ["deposits"], "language": "en"}
    ).json()["job"]
    jobs.Worker().run_next()
    failed = client.get(f"/jobs/{job['id']}", headers=headers).json()
    assert failed["status"] == "failed" and failed["result"] == {"http_status": 502}
    assert "OpenAI deep search failed" in failed["error"] and secret not in json.dumps(failed)


# Live key checks


class Checker:
    def __init__(self, status, detail="checked"):
        self.status, self.detail, self.seen = status, detail, []

    def check(self, provider, key):
        self.seen.append((provider, key))
        return KeyCheck(self.status, self.detail)


def test_a_rejected_key_is_not_stored_and_an_unchecked_one_is_not_ready(client):
    headers, project_id, _ = _study(client, "keys@example.com")
    runtime.key_checker = Checker("rejected", "OpenAI rejected the key.")
    rejected = client.post("/credentials", headers=headers, json={"provider": "openai", "label": "bad", "secret": "sk-rejected-0001", "scope": "byok"})
    assert rejected.status_code == 422 and rejected.json()["detail"] == "OpenAI rejected the key."
    assert [item["label"] for item in client.get("/credentials", headers=headers).json()["data"]] == ["primary"]

    runtime.key_checker = Checker("unreachable", "Could not reach OpenAI.")
    saved = client.post("/credentials", headers=headers, json={"provider": "openai", "label": "later", "secret": "sk-unchecked-0001", "scope": "byok"})
    assert saved.status_code == 200
    body = saved.json()
    assert body["ready"] is False and body["check_status"] == "unreachable" and body["checked_at"]
    run = _run(client, headers, project_id, body["id"])
    assert run.status_code == 422 and "not ready" in run.json()["detail"]

    runtime.key_checker = Checker("valid", "OpenAI accepted the key.")
    checked = client.post(f"/credentials/{body['id']}/check", headers=headers)
    assert checked.status_code == 200 and checked.json()["ready"] is True and checked.json()["check_detail"] == "OpenAI accepted the key."
    assert runtime.key_checker.seen == [("openai", "sk-unchecked-0001")]


def test_a_replacement_key_the_provider_rejects_leaves_the_old_one(client):
    headers, _, credential_id = _study(client, "rotate-rejected@example.com")
    before = client.get("/credentials", headers=headers).json()["data"][0]["fingerprint"]
    runtime.key_checker = Checker("rejected", "OpenAI rejected the key.")
    assert client.patch(f"/credentials/{credential_id}", headers=headers, json={"secret": "sk-rejected-rotation"}).status_code == 422
    after = client.get("/credentials", headers=headers).json()["data"][0]
    assert after["fingerprint"] == before and after["ready"] is True


@pytest.mark.parametrize(
    "provider, key, header, path",
    [
        ("openai", "sk-live-check-0001", ("authorization", "Bearer sk-live-check-0001"), "/v1/models"),
        ("anthropic", "sk-ant-live-check-01", ("x-api-key", "sk-ant-live-check-01"), "/v1/models"),
        ("google", "google-live-check-key-0001", ("x-goog-api-key", "google-live-check-key-0001"), "/v1beta/models"),
        ("xai", "xai-live-check-0001", ("authorization", "Bearer xai-live-check-0001"), "/v1/models"),
    ],
)
def test_check_key_reads_each_provider_with_its_own_header(provider, key, header, path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["header"] = request.headers.get(header[0])
        seen["query"] = str(request.url.query)
        return httpx.Response(200, json={"data": []})

    result = check_key(provider, key, transport=httpx.MockTransport(handler))
    assert result.status == "valid"
    assert seen["path"] == path and seen["header"] == header[1]
    assert key not in seen["query"]


@pytest.mark.parametrize(
    "provider, code, status",
    [("openai", 401, "rejected"), ("anthropic", 403, "rejected"), ("google", 400, "rejected"), ("openai", 400, "unreachable"),
     ("xai", 429, "valid"), ("openai", 500, "unreachable")],
)
def test_check_key_sorts_answers_into_valid_rejected_and_unreachable(provider, code, status):
    key = {"openai": "sk-x-000001", "anthropic": "sk-ant-x-000001", "google": "g" * 24, "xai": "xai-x-000001"}[provider]
    result = check_key(provider, key, transport=httpx.MockTransport(lambda request: httpx.Response(code, text=f"echo {key}")))
    assert result.status == status
    assert key not in result.detail


def test_check_key_reports_a_network_error_as_unreachable():
    def handler(request):
        raise httpx.ConnectError("down")

    assert check_key("openai", "sk-down-000001", transport=httpx.MockTransport(handler)).status == "unreachable"


# Demo quotas


def test_demo_accounts_have_daily_run_limits_and_users_do_not(client, monkeypatch):
    monkeypatch.setenv("DEMO_RUNS_PER_DAY", "2")
    headers, project_id, credential_id = _study(client, "demo-runs@example.com", kind="demo")
    for _ in range(2):
        assert _run(client, headers, project_id, credential_id, target_trajectory_count=4).status_code == 200
    refused = _run(client, headers, project_id, credential_id, target_trajectory_count=4)
    assert refused.status_code == 429
    assert "Demo accounts can start 2 runs a day" in refused.json()["detail"] and "UTC" in refused.json()["detail"]
    usage = client.get("/quota", headers=headers).json()["demo"]["daily"]["runs"]
    assert usage["used"] == 2 and usage["limit"] == 2 and usage["frees_at"]

    user, user_project, user_key = _study(client, "not-demo@example.com")
    for _ in range(3):
        assert _run(client, user, user_project, user_key, target_trajectory_count=4).status_code == 200
    assert client.get("/quota", headers=user).json() == {"kind": "user", "demo": None}


def test_a_demo_run_cancelled_before_it_starts_does_not_count(client, monkeypatch):
    monkeypatch.setenv("DEMO_RUNS_PER_DAY", "1")
    monkeypatch.setenv("JOBS_MODE", "worker")
    headers, project_id, credential_id = _study(client, "demo-cancel@example.com", kind="demo")
    first = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    assert client.post(f"/runs/{first['id']}/cancel", headers=headers).status_code == 200
    assert _run(client, headers, project_id, credential_id, target_trajectory_count=4).status_code == 200
    assert _run(client, headers, project_id, credential_id, target_trajectory_count=4).status_code == 429


def test_demo_runs_are_capped_in_size_counting_the_acceptance_ceiling(client, monkeypatch):
    monkeypatch.setenv("DEMO_MAX_SEQUENCES", "100")
    headers, project_id, credential_id = _study(client, "demo-size@example.com", kind="demo")
    too_big = _run(client, headers, project_id, credential_id, target_trajectory_count=30, group_size=4)
    assert too_big.status_code == 422 and "at most 100 sequences" in too_big.json()["detail"]
    ceiling = _run(client, headers, project_id, credential_id, target_trajectory_count=6, group_size=4, target_kind="accepted_groups")
    assert ceiling.status_code == 422 and "asks for 120" in ceiling.json()["detail"]
    assert _run(client, headers, project_id, credential_id, target_trajectory_count=5, group_size=4, target_kind="accepted_groups").status_code == 200


def test_demo_judge_cycles_and_deep_searches_have_daily_limits(client, monkeypatch):
    monkeypatch.setenv("DEMO_JUDGE_CYCLES_PER_DAY", "1")
    monkeypatch.setenv("DEMO_DEEP_SEARCHES_PER_DAY", "1")
    headers, project_id, credential_id = _study(client, "demo-judge@example.com", kind="demo")
    runtime.searcher = Searcher()
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=4, max_cycles=3).json()
    # With no engine configured the attempt fails, and a failed job does not count.
    assert client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).status_code == 503
    assert client.get("/quota", headers=headers).json()["demo"]["daily"]["judge_cycles"]["used"] == 0
    runtime.judge = RecordingJudge()
    assert client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).status_code == 200
    judged = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert judged.status_code == 429 and "1 judge cycle a day" in judged.json()["detail"]
    search = {"credential_id": credential_id, "sub_domains": ["deposits"], "language": "en"}
    assert client.post(f"/projects/{project_id}/deep-search", headers=headers, json=search).status_code == 200
    assert client.post(f"/projects/{project_id}/deep-search", headers=headers, json=search).status_code == 429


# Oversampling stays inside the run limit


def test_oversampling_never_draws_past_the_run_limit(client, monkeypatch, tmp_path):
    import app.generation as generation
    from app import store
    from sectors import rewards

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(generation, "MAX_RUN_SEQUENCES", 40)
    # No group is ever accepted, so only the limit can stop the draw.
    monkeypatch.setattr(rewards, "group_accepted", lambda passed: False if len(passed) > 1 else None)
    store._read_json.cache_clear()
    headers, project_id, credential_id = _study(client, "run-limit@example.com")
    created = _run(client, headers, project_id, credential_id, target_trajectory_count=10, group_size=2, target_kind="accepted_groups",
                   sub_domains=["onboarding_and_kyc"], min_events=1, max_events=4, event_budget=None)
    assert created.status_code == 200, created.text
    bucket = created.json()["generation"]["target"]["buckets"][0]
    assert bucket["generated"] == 20 and bucket["stopped_by"] == "acceptance"
