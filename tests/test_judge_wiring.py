import dataclasses
import json

import httpx
import pytest

from app import runtime
from app.judge import EvalNotConfigured, InferenceEngineClient, JudgeUnavailable, normalize_base_url
from app.settings import SettingsError, check_startup, load_settings
from test_api import _auth, _link, _project, _ready_key, _run

VERDICT = {
    "id": "eval_1",
    "object": "eval",
    "created": 1,
    "rubric": "safety",
    "judge_model": "qwen3.8:27b",
    "verdict": {"score": 1, "parsed": {"safe": True}, "raw": "{}", "parse_status": "clean"},
    "duration_ms": 4,
}


def _client(handler, sleeps=None, api_key="sk-tenant-secret", base_url="http://engine.test"):
    return InferenceEngineClient(
        base_url=base_url,
        api_key=api_key,
        tenant="domain-trajectory-data-generation",
        org_id="org-trajdata",
        key_id="domain-trajectory-data-generation-primary",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


@pytest.mark.parametrize(
    "raw",
    [
        "https://engine.test",
        "https://engine.test/",
        "https://engine.test/v1",
        "https://engine.test/v1/",
        "https://engine.test/v1.",
        " https://engine.test/v1. ",
    ],
)
def test_base_url_is_reduced_to_the_origin(raw):
    assert normalize_base_url(raw) == "https://engine.test"


@pytest.mark.parametrize("raw", ["engine.test", "ftp://engine.test", "https://", "/v1"])
def test_malformed_base_url_is_refused(raw):
    with pytest.raises(EvalNotConfigured):
        normalize_base_url(raw)


def test_trailing_v1_dot_no_longer_doubles_the_path():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=VERDICT)

    client = _client(handler, base_url="http://engine.test/v1.")
    verdict = client.run_eval(rubric="safety", prompt="p", response="r")
    assert seen["path"] == "/v1/evals/run"
    assert seen["body"]["judge_model"] == "qwen3.8:27b"
    assert verdict["judge_model"] == "qwen3.8:27b"
    client.close()


def test_busy_engine_is_retried_once_after_retry_after():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "tenant_queue_full"}})
        return httpx.Response(200, json=VERDICT)

    client = _client(handler, sleeps)
    assert client.run_eval(rubric="safety", prompt="p", response="r")["score"] == 1
    assert len(calls) == 2
    assert sleeps == [3.0]
    client.close()


def test_second_busy_answer_becomes_503_with_request_id():
    def handler(request):
        return httpx.Response(503, headers={"retry-after": "1", "x-request-id": "req-42"}, json={})

    client = _client(handler, [])
    with pytest.raises(JudgeUnavailable) as caught:
        client.run_eval(rubric="safety", prompt="p", response="r")
    assert caught.value.status == 503
    assert "req-42" in caught.value.detail()
    client.close()


def test_long_retry_after_is_not_waited_out():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "600"}, json={})

    client = _client(handler, sleeps)
    with pytest.raises(JudgeUnavailable):
        client.run_eval(rubric="safety", prompt="p", response="r")
    assert len(calls) == 1
    assert sleeps == []
    client.close()


def test_rejected_key_and_engine_errors_map_to_502_without_the_key():
    def rejected(request):
        return httpx.Response(401, json={"detail": "invalid key"})

    client = _client(rejected)
    with pytest.raises(JudgeUnavailable) as caught:
        client.run_eval(rubric="safety", prompt="p", response="r")
    assert caught.value.status == 502
    assert "INFERENCE_ENGINE_API_KEY" in caught.value.detail()
    client.close()

    def broken(request):
        return httpx.Response(500, json={"error": {"message": "boom with sk-tenant-secret inside"}})

    client = _client(broken)
    with pytest.raises(JudgeUnavailable) as caught:
        client.run_eval(rubric="safety", prompt="p", response="r")
    assert caught.value.status == 502
    assert "sk-tenant-secret" not in caught.value.detail()
    client.close()


def test_timeout_and_unreachable_engine():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = _client(slow)
    with pytest.raises(JudgeUnavailable) as caught:
        client.run_eval(rubric="safety", prompt="p", response="r")
    assert caught.value.status == 504
    client.close()

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    client = _client(down)
    with pytest.raises(JudgeUnavailable) as caught:
        client.run_eval(rubric="safety", prompt="p", response="r")
    assert caught.value.status == 503
    client.close()


class FailingJudge:
    def run_eval(self, **kwargs):
        raise JudgeUnavailable(504, "The judge timed out.", "req-7")


def test_judge_failure_reaches_the_studio_as_its_status_and_stores_no_cycle(client):
    headers = _auth(client, "timeout@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    run_id = _run(client, headers, project_id, credential_id).json()["id"]
    runtime.judge = FailingJudge()
    response = client.post(f"/runs/{run_id}/evaluate", headers=headers, json={})
    assert response.status_code == 504
    assert "req-7" in response.json()["detail"]
    assert client.get(f"/runs/{run_id}", headers=headers).json()["cycle_count"] == 0


def test_startup_refuses_the_dev_jwt_secret_and_a_malformed_engine_url(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("TRAJ_DEV_MODE", raising=False)
    with pytest.raises(SettingsError):
        check_startup(load_settings())
    monkeypatch.setenv("TRAJ_DEV_MODE", "1")
    check_startup(load_settings())

    cfg = dataclasses.replace(load_settings(), inference_base_url="engine-without-scheme")
    with pytest.raises(SettingsError):
        check_startup(cfg)


def test_old_key_id_variable_is_still_read(monkeypatch):
    monkeypatch.delenv("INFERENCE_ENGINE_KEY_ID", raising=False)
    monkeypatch.setenv("INFERENCE_ENGINE_API_KEY_ID", "legacy-key-id")
    assert load_settings().inference_key_id == "legacy-key-id"


def test_keys_can_be_replaced_and_deleted_by_their_owner_only(client):
    owner = _auth(client, "owner@example.com", "password-123")
    other = _auth(client, "other@example.com", "password-123")
    key_id = _ready_key(client, owner, secret="sk-first-secret-0001")
    before = client.get("/credentials", headers=owner).json()["data"][0]

    assert client.patch(f"/credentials/{key_id}", headers=other, json={"secret": "sk-stolen-000001"}).status_code == 404
    assert client.delete(f"/credentials/{key_id}", headers=other).status_code == 404
    assert client.patch(f"/credentials/{key_id}", headers=owner, json={}).status_code == 422
    assert client.patch(f"/credentials/{key_id}", headers=owner, json={"secret": "not-an-openai-key"}).status_code == 422

    replaced = client.patch(f"/credentials/{key_id}", headers=owner, json={"secret": "sk-second-secret-0002"})
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["label"] == before["label"]
    assert body["fingerprint"] != before["fingerprint"]
    assert "sk-second-secret-0002" not in replaced.text

    renamed = client.patch(f"/credentials/{key_id}", headers=owner, json={"label": "renamed"})
    assert renamed.json()["label"] == "renamed"
    assert renamed.json()["fingerprint"] == body["fingerprint"]

    assert client.delete(f"/credentials/{key_id}", headers=owner).status_code == 204
    assert client.get("/credentials", headers=owner).json()["data"] == []


class HalfReadableJudge:
    def run_eval(self, **kwargs):
        if kwargs["rubric"] == "safety":
            return {"score": 1, "parsed": {"safe": True}, "raw": "{}", "readable": True, "judge_model": "qwen3.8:27b", "duration_ms": 1}
        return {"score": 0, "parsed": {}, "raw": "", "readable": False, "judge_model": "qwen3.8:27b", "duration_ms": 1}


def test_unreadable_verdict_is_unscored_and_adds_no_revision_note(client):
    headers = _auth(client, "unreadable@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    run_id = _run(client, headers, project_id, credential_id).json()["id"]
    runtime.judge = HalfReadableJudge()
    body = client.post(f"/runs/{run_id}/evaluate", headers=headers, json={}).json()
    cycle = body["cycles"][-1]
    assert cycle["accepted"] is False
    assert cycle["revision_notes"] == []
    unreadable = {v["rubric"] for v in cycle["verdicts"] if v["readable"] is False}
    assert "helpfulness" in unreadable and "safety" not in unreadable
    assert all(v["score"] is None for v in cycle["verdicts"] if v["rubric"] in unreadable)


def test_engine_parse_failure_is_reported_as_unreadable():
    def handler(request):
        return httpx.Response(200, json={**VERDICT, "verdict": {"score": 0, "parsed": {}, "raw": "", "parse_status": "failed"}})

    client = _client(handler)
    assert client.run_eval(rubric="helpfulness", prompt="p", response="r")["readable"] is False
    client.close()
