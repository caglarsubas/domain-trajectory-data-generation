"""A judge the stack can reach (Slice 17): the judge is checked before a cycle, and a cycle that would fail is refused
with the reason instead of queued (decision 24)."""

import dataclasses
import json

import httpx
import pytest

from app import judge_status, runtime
from app.settings import load_settings
from test_api import RecordingJudge, _admin, _auth, _link, _project, _ready_key, _run
from test_judge_acceptance import OneModel, TwoModels

KEY = "engine-platform-key-0001"
LISTED = ["gemma4:26b", "ministral-3:8b", "qwen3.6:27b"]


def _engine(health=200, ready=True, rubrics=200, models=None, unavailable=(), seen=None):
    def handler(request):
        if seen is not None:
            seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/v1/health":
            return httpx.Response(health, json={"status": "ok", "ready": ready, "version": "0.1.13", "readiness": {"message": "loading models"}})
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"detail": "invalid key"})
        if request.url.path == "/v1/evals/rubrics":
            return httpx.Response(rubrics, json={"data": [{"name": "helpfulness"}, {"name": "safety"}]})
        if request.url.path == "/v1/models":
            listed = LISTED if models is None else models
            return httpx.Response(200, json={"data": [{"id": model} for model in listed], "unavailable": list(unavailable)})
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.MockTransport(handler)


def _down(request):
    raise httpx.ConnectError("refused", request=request)


def _cfg(**overrides):
    values = {"inference_base_url": "http://host.docker.internal:8080", "inference_api_key": KEY, **overrides}
    return dataclasses.replace(load_settings(), **values)


@pytest.fixture(autouse=True)
def _fresh():
    judge_status.forget()
    yield
    judge_status.forget()


def _by_name(found):
    return {item["name"]: item for item in found["checks"]}


def test_a_ready_engine_answers_all_three_questions():
    seen = []
    found = judge_status.check(_cfg(), transport=_engine(seen=seen))
    assert found["ready"] is True and found["reason"] == ""
    assert found["models"] == ["qwen3.6:27b", "gemma4:26b"]
    checks = _by_name(found)
    assert [item["label"] for item in found["checks"]] == ["Engine", "Rubric registry", "Judge models"]
    assert checks["engine"]["detail"] == "Engine 0.1.13 is ready."
    assert checks["rubrics"]["detail"] == "2 rubrics listed."
    assert checks["models"]["detail"] == "qwen3.6:27b and gemma4:26b are listed."
    # The health check carries no key; the registry and the model list carry the platform key.
    assert dict(seen) == {"/v1/health": None, "/v1/evals/rubrics": f"Bearer {KEY}", "/v1/models": f"Bearer {KEY}"}
    assert KEY not in json.dumps(found)


def test_an_unreachable_engine_is_said_and_nothing_else_is_asked():
    found = judge_status.check(_cfg(), transport=httpx.MockTransport(_down))
    assert found["ready"] is False
    assert found["reason"].startswith("No engine answered at INFERENCE_ENGINE_BASE_URL.")
    checks = _by_name(found)
    assert checks["rubrics"]["ok"] is None and checks["models"]["ok"] is None


def test_an_offline_tunnel_is_told_from_an_engine():
    # An offline ngrok tunnel answers every path with 404.
    found = judge_status.check(_cfg(), transport=_engine(health=404))
    assert found["ready"] is False and "not llm_inference_engine" in found["reason"] and "404" in found["reason"]


def test_a_starting_engine_is_not_ready():
    found = judge_status.check(_cfg(), transport=_engine(ready=False))
    assert found["reason"] == "The engine is starting: loading models."


def test_a_rejected_key_and_a_missing_registry_are_named():
    found = judge_status.check(_cfg(inference_api_key="wrong-key"), transport=_engine())
    assert found["reason"] == "The engine rejected the platform key. Check INFERENCE_ENGINE_API_KEY."
    found = judge_status.check(_cfg(), transport=_engine(rubrics=404))
    assert found["reason"].startswith("The engine has no rubric registry.")
    assert _by_name(found)["models"]["ok"] is True


def test_a_judge_model_the_engine_does_not_list_is_named_with_its_reason():
    found = judge_status.check(_cfg(), transport=_engine(models=["qwen3.6:27b"]))
    assert found["ready"] is False
    assert found["reason"].startswith("gemma4:26b is not among the 1 models the engine lists.")
    unavailable = [{"id": "gemma4:26b", "reason": "no_local_model_layer", "detail": "manifest has no model layer"}]
    found = judge_status.check(_cfg(), transport=_engine(models=["qwen3.6:27b"], unavailable=unavailable))
    assert found["reason"].startswith("gemma4:26b is listed as unavailable: manifest has no model layer.")
    # A primary judge alone needs only its own model.
    alone = judge_status.check(_cfg(second_judge_model=""), transport=_engine(models=["qwen3.6:27b"]))
    assert alone["ready"] is True and _by_name(alone)["models"]["detail"] == "qwen3.6:27b is listed."


def test_an_unconfigured_judge_says_which_setting_is_missing():
    found = judge_status.check(_cfg(inference_api_key=""))
    assert found["reason"] == "The platform judge is not configured on this server: INFERENCE_ENGINE_API_KEY is missing."
    found = judge_status.check(_cfg(inference_base_url=""))
    assert found["reason"].endswith("INFERENCE_ENGINE_BASE_URL is missing.")


def test_a_check_is_kept_briefly_and_checked_again_on_request(monkeypatch):
    seen = []
    monkeypatch.setattr(runtime, "engine_transport", _engine(seen=seen))
    cfg = _cfg()
    assert judge_status.status(cfg)["ready"] is True
    judge_status.status(cfg)
    assert len(seen) == 3
    judge_status.status(cfg, fresh=True)
    assert len(seen) == 6


# The studio's routes


def _configured(monkeypatch, transport):
    monkeypatch.setenv("INFERENCE_ENGINE_BASE_URL", "http://host.docker.internal:8080")
    monkeypatch.setenv("INFERENCE_ENGINE_API_KEY", KEY)
    monkeypatch.setattr(runtime, "engine_transport", transport)


def _study(client, email, kind="user"):
    headers = _auth(client, email, "password-123", kind=kind)
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    run = _run(client, headers, project_id, _ready_key(client, headers), target_trajectory_count=8, event_budget=None)
    assert run.status_code == 200, run.text
    return headers, project_id, run.json()


def test_the_study_page_hears_why_the_judge_cannot_run_and_the_cycle_is_refused(client, monkeypatch):
    _configured(monkeypatch, httpx.MockTransport(_down))
    headers, project_id, run = _study(client, "unreachable@example.com")
    found = client.get(f"/projects/{project_id}/judge", headers=headers)
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["ready"] is False and body["reason"].startswith("No engine answered")
    assert body["last_cycle"] is None and "address" not in body
    assert KEY not in found.text

    refused = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert refused.status_code == 503 and refused.json()["detail"] == body["reason"]
    after = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert after["judge_job"] is None and after["cycle_count"] == 0
    assert client.post(f"/runs/{run['id']}/rubric-proposals", headers=headers).status_code == 503


def test_a_reachable_engine_lets_the_cycle_through(client, monkeypatch):
    _configured(monkeypatch, _engine())
    headers, project_id, run = _study(client, "reachable@example.com")
    assert client.get(f"/projects/{project_id}/judge", headers=headers).json()["ready"] is True
    # The in-process judge stands in for the engine's verdicts once the check has passed.
    judge_status.forget()
    runtime.judge = RecordingJudge()
    runtime.judge.status = lambda: judge_status.check(load_settings(), transport=_engine())
    evaluated = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["cycle_count"] == 1


def test_regenerating_asks_the_judge_so_it_is_refused_when_the_judge_cannot_run(client, monkeypatch):
    headers, project_id, run = _study(client, "regen-refused@example.com")
    runtime.judge = RecordingJudge(scores={"helpfulness": 1, "correctness": 0.25, "safety": 1, "pairwise_quality": 0})
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    assert cycle["accepted"] is False
    runtime.judge = None
    _configured(monkeypatch, _engine(health=404))
    refused = client.post(f"/runs/{run['id']}/regenerate", headers=headers, json={})
    assert refused.status_code == 503 and "not llm_inference_engine" in refused.json()["detail"]
    assert [item["id"] for item in client.get("/runs", headers=headers).json()["data"]] == [run["id"]]


def test_the_admin_sees_where_the_studio_looks(client, monkeypatch):
    monkeypatch.setenv("INFERENCE_ENGINE_BASE_URL", "http://user:secret@host.docker.internal:8080/v1")
    monkeypatch.setenv("INFERENCE_ENGINE_API_KEY", KEY)
    monkeypatch.setattr(runtime, "engine_transport", _engine())
    headers = _admin(client)
    project_id = _project(client, headers)
    body = client.get(f"/projects/{project_id}/judge", headers=headers).json()
    assert body["address"] == "http://host.docker.internal:8080"
    assert "secret" not in json.dumps(body)


def test_the_study_page_says_before_the_next_cycle_that_one_model_served_both_judges(client, monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "3")
    headers, project_id, run = _study(client, "one-model-before@example.com")
    runtime.judge = OneModel()
    assert client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).status_code == 200
    last = client.get(f"/projects/{project_id}/judge", headers=headers).json()["last_cycle"]
    assert last["run_id"] == run["id"]
    assert last["same_model"] == ["gemma4:26b"] and last["same_judges"] is True
    assert last["served_by"] == {"qwen3.6:27b": ["gemma4:26b"], "gemma4:26b": ["gemma4:26b"]}

    # Once the judge models change, the last cycle's pairing says nothing about the next.
    monkeypatch.setenv("INFERENCE_ENGINE_SECOND_JUDGE_MODEL", "ministral-3:8b")
    assert client.get(f"/projects/{project_id}/judge", headers=headers).json()["last_cycle"]["same_judges"] is False

    # A later cycle served by two models replaces it.
    monkeypatch.delenv("INFERENCE_ENGINE_SECOND_JUDGE_MODEL")
    second = _run(client, headers, project_id, _ready_key(client, headers, secret="sk-good-second-key-0002"), target_trajectory_count=8, event_budget=None).json()
    runtime.judge = TwoModels()
    assert client.post(f"/runs/{second['id']}/evaluate", headers=headers, json={}).status_code == 200
    last = client.get(f"/projects/{project_id}/judge", headers=headers).json()["last_cycle"]
    assert last["run_id"] == second["id"] and last["same_model"] == []
