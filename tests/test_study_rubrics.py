"""Study rubrics (4B): proposed by the judge from a group, reviewed and edited, registered, and asked in a cycle."""

import json
import re

import httpx
import pytest

from app import runtime
from app.judge import InferenceEngineClient, RubricLimitReached
from app.judging import register
from app.models import StudyRubric
from app.study_rubrics import MAX_CRITERIA, TEXT_CHARS, TITLE_CHARS, clean, definition
from test_api import _auth, _link, _project, _ready_key, _run

PRIMARY = "qwen3.6:27b"

PROPOSALS = {
    "solution": {
        "title": "Credit decision settled",
        "description": "The application ends approved and funded, or declined with a reason.",
        "criteria": ["The credit decision is made before the journey ends.", "An approved loan is disbursed.", "A decline gives a reason."],
        "anchors": {"5": "Decided and closed cleanly.", "3": "Decided but left untidy.", "1": "Left undecided."},
        "fit": 4,
    },
    "behavior": {
        "title": "Prompt, forward-only path",
        "description": "Steps move forward without rework and waits stay short.",
        "criteria": ["No step returns an account to an earlier state.", "KYC completes within two days of the application."],
        "anchors": {"5": "No rework and prompt waits.", "3": "Some slow waits.", "1": "Repeated rework."},
        "fit": 3,
    },
}


class StudyJudge:
    """Writes the proposals above, scores study rubrics low, and answers everything with as many repeats as asked."""

    def __init__(self, proposals=None):
        self.calls = []
        self.registered = []
        self.deleted = []
        self.proposals = proposals or PROPOSALS

    def register_rubric(self, definition):
        self.registered.append(definition)
        return {"name": definition["name"], "digest": "sha256:" + definition["name"]}

    def run_eval(self, *, rubric, judge_model=None, repeats=1, temperature=0.0, **payload):
        rubric = rubric.removeprefix("trajectory_")  # the studio asks its own copies of these rubrics (decision 28)
        self.calls.append({"rubric": rubric, "model": judge_model, **payload})
        if rubric == "rubric_proposal":
            kind = "solution" if "solution rubric" in payload["prompt"] else "behavior"
            parsed = self.proposals[kind]
            return {"score": 0.75, "parsed": parsed, "raw": json.dumps(parsed), "readable": True, "judge_model": judge_model, "rubric_digest": "sha256:proposal"}
        base = 0.25 if rubric.startswith("study_") else {"helpfulness": 4, "correctness": 1, "safety": 1, "pairwise_quality": 0.5, "process_conformance": 1.0, "decision_score": 1.0}[rubric]
        verdicts = [{"score": base, "parsed": {"reason": f"{rubric} {index}"}, "raw": "{}", "readable": True} for index in range(repeats)]
        return {**verdicts[0], "verdicts": verdicts, "judge_model": judge_model, "duration_ms": 2}


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id, _ready_key(client, headers)


@pytest.fixture()
def small_sample(monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")


# ---------------------------------------------------------------------------
# Text and definitions
# ---------------------------------------------------------------------------


def test_an_owners_edit_is_checked_strictly_and_the_judges_proposal_is_trimmed():
    good = {k: v for k, v in PROPOSALS["solution"].items() if k != "fit"}
    assert clean(good, strict=True)["criteria"] == good["criteria"]
    long = {**good, "title": "t " * 100, "criteria": [f"criterion {i} " + "word " * 80 for i in range(9)]}
    with pytest.raises(ValueError, match="title is longer"):
        clean({**good, "title": long["title"]}, strict=True)
    with pytest.raises(ValueError, match="criterion is longer"):
        clean(long, strict=True)
    trimmed = clean(long, strict=False)
    assert len(trimmed["title"]) <= TITLE_CHARS + 1 and trimmed["title"].endswith("…")
    assert len(trimmed["criteria"]) == MAX_CRITERIA and all(len(item) <= TEXT_CHARS + 1 for item in trimmed["criteria"])
    with pytest.raises(ValueError, match="at least 2 criteria"):
        clean({**good, "criteria": ["only one"]}, strict=True)
    with pytest.raises(ValueError, match="anchor 3"):
        clean({**good, "anchors": {"5": "a", "1": "c"}}, strict=True)
    # Criteria typed one per line are split, bullets and blanks dropped.
    assert clean({**good, "criteria": "- first\n\n• second\n"}, strict=True)["criteria"] == ["first", "second"]


def test_a_definition_is_named_by_its_content_and_stays_inside_the_engines_limits():
    fields = clean({k: v for k, v in PROPOSALS["solution"].items() if k != "fit"}, strict=True)
    rubric = StudyRubric(kind="solution", **fields)
    first = definition(rubric, "Banking")
    assert re.fullmatch(r"study_solution_[0-9a-f]{12}", first["name"])
    assert all(criterion in first["system_prompt"] for criterion in fields["criteria"])
    assert "Score 5: Decided and closed cleanly." in first["system_prompt"]
    assert first["user_prompt_template"].count("{") == 2
    assert definition(rubric, "Banking") == first
    rubric.criteria = fields["criteria"][:2]
    assert definition(rubric, "Banking")["name"] != first["name"]
    largest = StudyRubric(kind="behavior", title="t" * TITLE_CHARS, description="d" * TEXT_CHARS, criteria=["c" * TEXT_CHARS] * MAX_CRITERIA,
                          anchors={score: "a" * TEXT_CHARS for score in ("5", "3", "1")})
    assert len(definition(largest, "Insurance")["system_prompt"]) < 8000


# ---------------------------------------------------------------------------
# Propose, review, register, use
# ---------------------------------------------------------------------------


def test_a_rubric_proposed_from_a_group_is_reviewed_registered_and_used_in_a_cycle(client, small_sample):
    headers, project_id, credential_id = _study(client, "study-rubrics@example.com")
    run = _run(client, headers, project_id, credential_id, group_size=4, target_trajectory_count=8, event_budget=None).json()
    judge = runtime.judge = StudyJudge()

    proposed = client.post(f"/runs/{run['id']}/rubric-proposals", headers=headers)
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["proposal_job"]["status"] == "succeeded"
    rubrics = {item["kind"]: item for item in body["study_rubrics"]}
    assert set(rubrics) == {"solution", "behavior"} and {item["status"] for item in rubrics.values()} == {"proposed"}
    source = rubrics["solution"]["source"]
    # The judge studied one group: its primary and rollouts, with both outcomes where the group has them.
    assert source["group"] in source["trajectory_ids"] and 2 <= len(source["trajectory_ids"]) <= 4
    assert source["fit"] == 4 and source["judge_model"] == PRIMARY and source["proposal_digest"] == "sha256:proposal"
    asked = [call for call in judge.calls if call["rubric"] == "rubric_proposal"]
    assert len(asked) == 2 and "Sector: banking" in asked[0]["prompt"] and "solution rubric" in asked[0]["prompt"]
    assert asked[0]["response"].startswith("--- Journey 1 of") and asked[0]["response"] == asked[1]["response"]
    assert [item["name"] for item in judge.registered] == ["rubric_proposal"]

    # Review: an edit is checked, and puts the rubric back to proposed until it is approved.
    bad = client.patch(f"/projects/{project_id}/rubrics/{rubrics['solution']['id']}", headers=headers, json={"criteria": ["one"]})
    assert bad.status_code == 422 and "at least 2 criteria" in bad.json()["detail"]
    edited = client.patch(f"/projects/{project_id}/rubrics/{rubrics['solution']['id']}", headers=headers,
                          json={"criteria": PROPOSALS["solution"]["criteria"] + ["The customer is told the outcome."]})
    assert edited.status_code == 200 and edited.json()["edited"] is True and edited.json()["status"] == "proposed"
    for rubric in rubrics.values():
        approved = client.post(f"/projects/{project_id}/rubrics/{rubric['id']}/approve", headers=headers)
        assert approved.status_code == 200 and approved.json()["status"] == "approved"

    # Used: the cycle registers both, asks them of every sampled journey, and compares them with the code's rubrics.
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    names = sorted(item["engine_name"] for item in client.get(f"/projects/{project_id}/rubrics", headers=headers).json()["data"])
    assert cycle["judging"]["study"] == names
    assert {cycle["judging"]["rubrics"][name]["source"] for name in names} == {"study"}
    assert {name for name in names} <= {item["name"] for item in judge.registered}
    for name in names:
        calls = [call for call in judge.calls if call["rubric"] == name]
        assert len(calls) == 2 * 2 and all("study's" in call["prompt"] for call in calls)
        found = cycle["agreement"]["code"][name]
        assert found["study"] is True and found["signal"] in {"solution_rubric", "behavior_rubric"}
        assert found["models"][PRIMARY]["mean"] == 0.25
        assert cycle["agreement"]["repeats"][name][PRIMARY]["calls"] == 2
        assert all(item["rubric_digest"] == f"sha256:{name}" for item in cycle["verdicts"] if item["rubric"] == name)
    # Scored low by the judge, the study rubrics still decide nothing.
    assert cycle["accepted"] is True and not any(name in cycle["scores"] for name in names)

    # The same rubrics do not reopen the run; a newly approved one does.
    again = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert again.status_code == 409
    client.patch(f"/projects/{project_id}/rubrics/{rubrics['behavior']['id']}", headers=headers, json={"title": "Forward-only, prompt path"})
    client.post(f"/projects/{project_id}/rubrics/{rubrics['behavior']['id']}/approve", headers=headers)
    assert client.get(f"/runs/{run['id']}", headers=headers).json()["study_rubrics_pending"] is True
    second = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert second.status_code == 200 and len(second.json()["cycles"]) == 2
    assert second.json()["study_rubrics_pending"] is False
    assert second.json()["cycles"][1]["judging"]["study"] != cycle["judging"]["study"]


def test_approving_a_second_rubric_of_a_kind_retires_the_first_and_a_new_proposal_keeps_edits(client):
    headers, project_id, credential_id = _study(client, "retire@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None).json()
    runtime.judge = StudyJudge()
    first = {item["kind"]: item for item in client.post(f"/runs/{run['id']}/rubric-proposals", headers=headers).json()["study_rubrics"]}
    client.post(f"/projects/{project_id}/rubrics/{first['solution']['id']}/approve", headers=headers)
    client.patch(f"/projects/{project_id}/rubrics/{first['behavior']['id']}", headers=headers, json={"title": "Kept by the owner"})

    runtime.judge = StudyJudge({**PROPOSALS, "solution": {**PROPOSALS["solution"], "title": "Resolved either way"}})
    rows = client.post(f"/runs/{run['id']}/rubric-proposals", headers=headers).json()["study_rubrics"]
    titles = {(item["kind"], item["title"], item["status"]) for item in rows}
    # The approved rubric stays, the owner's edited proposal stays, and the new proposals join them.
    assert ("solution", "Credit decision settled", "approved") in titles
    assert ("behavior", "Kept by the owner", "proposed") in titles
    assert ("solution", "Resolved either way", "proposed") in titles
    newer = next(item for item in rows if item["title"] == "Resolved either way")
    client.post(f"/projects/{project_id}/rubrics/{newer['id']}/approve", headers=headers)
    statuses = {item["title"]: item["status"] for item in client.get(f"/projects/{project_id}/rubrics", headers=headers).json()["data"]}
    assert statuses["Credit decision settled"] == "retired" and statuses["Resolved either way"] == "approved"


def test_an_unreadable_proposal_fails_the_job_and_stores_nothing(client):
    class Unreadable(StudyJudge):
        def run_eval(self, *, rubric, **kwargs):
            if rubric == "rubric_proposal":
                return {"score": 0.0, "parsed": {}, "raw": "", "readable": False, "judge_model": PRIMARY}
            return super().run_eval(rubric=rubric, **kwargs)

    headers, project_id, credential_id = _study(client, "unreadable-proposal@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None).json()
    runtime.judge = Unreadable()
    response = client.post(f"/runs/{run['id']}/rubric-proposals", headers=headers)
    assert response.status_code == 502 and "could not be read" in response.json()["detail"]
    assert client.get(f"/projects/{project_id}/rubrics", headers=headers).json()["data"] == []


def test_a_judge_without_a_registry_cannot_propose(client):
    headers, project_id, credential_id = _study(client, "no-registry-proposal@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None).json()

    class Plain:
        def run_eval(self, **kwargs):
            raise AssertionError("not reached")

    runtime.judge = Plain()
    response = client.post(f"/runs/{run['id']}/rubric-proposals", headers=headers)
    assert response.status_code == 502 and "does not accept rubrics" in response.json()["detail"]


def test_another_account_cannot_see_or_change_a_studys_rubrics(client):
    headers, project_id, credential_id = _study(client, "owner-rubrics@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None).json()
    runtime.judge = StudyJudge()
    rubric = client.post(f"/runs/{run['id']}/rubric-proposals", headers=headers).json()["study_rubrics"][0]
    other = _auth(client, "stranger-rubrics@example.com", "password-123")
    assert client.get(f"/projects/{project_id}/rubrics", headers=other).status_code == 404
    assert client.post(f"/projects/{project_id}/rubrics/{rubric['id']}/approve", headers=other).status_code == 404
    assert client.delete(f"/projects/{project_id}/rubrics/{rubric['id']}", headers=other).status_code == 404
    assert client.delete(f"/projects/{project_id}/rubrics/{rubric['id']}", headers=headers).status_code == 204
    assert client.get(f"/projects/{project_id}/rubrics", headers=headers).json()["data"][0]["id"] != rubric["id"]


def test_a_full_engine_frees_only_study_rubrics_no_study_still_uses(client):
    from app.db import SessionLocal

    class Full(StudyJudge):
        def __init__(self):
            super().__init__()
            self.full = True

        def register_rubric(self, definition):
            if self.full:
                self.full = False
                raise RubricLimitReached("The judge's engine holds its limit of 64 rubrics for this platform.")
            return super().register_rubric(definition)

        def list_rubrics(self):
            return [
                {"name": "safety", "source": "builtin"},
                {"name": "process_conformance", "source": "tenant"},
                {"name": "study_solution_aaaaaaaaaaaa", "source": "tenant"},
                {"name": "study_behavior_bbbbbbbbbbbb", "source": "tenant"},
            ]

        def delete_rubric(self, name):
            self.deleted.append(name)
            return True

    judge = Full()
    with SessionLocal() as db:
        digest = register(judge, {"name": "study_solution_cccccccccccc"}, db)
    assert digest == "sha256:study_solution_cccccccccccc"
    assert judge.deleted == ["study_solution_aaaaaaaaaaaa", "study_behavior_bbbbbbbbbbbb"]


def test_the_client_lists_rubrics_and_names_a_full_engine():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [{"name": "safety", "source": "builtin"}]})
        detail = {"message": "a tenant may register at most 64 rubrics", "type": "rubric_limit_reached", "limit": 64}
        return httpx.Response(409, json={"detail": detail, "error": detail})

    client = InferenceEngineClient(
        base_url="http://engine.test", api_key="sk-tenant-secret", tenant="t", org_id="o", key_id="k",
        transport=httpx.MockTransport(handler), sleep=lambda _: None,
    )
    assert client.list_rubrics() == [{"name": "safety", "source": "builtin"}]
    with pytest.raises(RubricLimitReached, match="limit of 64 rubrics"):
        client.register_rubric({"name": "study_x"})
    client.close()


def test_demo_accounts_have_a_daily_proposal_limit(client, monkeypatch):
    monkeypatch.setenv("DEMO_RUBRIC_PROPOSALS_PER_DAY", "1")
    headers = _auth(client, "demo-proposals@example.com", "password-123", kind="demo")
    daily = client.get("/quota", headers=headers).json()["demo"]["daily"]
    assert daily["rubric_proposals"] == {"used": 0, "limit": 1, "frees_at": None}
