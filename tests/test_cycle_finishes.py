"""A cycle that finishes (Slice 20, decision 27): a judge call the engine timed out or was too busy for is asked once
more, and a cycle that stops keeps its answers and is resumed rather than restarted."""

import pytest

from app import runtime
from app.evaluation import CORRECTNESS_QUESTION, HELPFULNESS_QUESTION, SAFETY_QUESTION, evaluate_journeys
from app.judge import JudgeUnavailable
from app.judging import Answers, answers_path
from app.models import Run
from sectors.registry import get_sector
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run

BANKING = get_sector("banking")


class Flaky(RecordingJudge):
    """Fails the calls whose numbers are given, each with its status, once per listed occurrence."""

    def __init__(self, failures: dict[int, list[int]]):
        super().__init__()
        self.failures = {number: list(statuses) for number, statuses in failures.items()}
        self.attempts = 0

    def run_eval(self, **kwargs):
        self.attempts += 1
        number = len(self.calls) + 1
        pending = self.failures.get(number)
        if pending:
            status = pending.pop(0)
            raise JudgeUnavailable(status, "The judge timed out after 240 seconds." if status == 504 else "The judge is busy or starting.")
        return super().run_eval(**kwargs)


def _journeys(count=2):
    from app.judging import sample_entries
    from app.store import DbStore
    from trajectory_contract import TrajectoryBundle

    bundle = BANKING.generate(sub_domains=["onboarding_and_kyc", "deposits"], language="en", target_trajectory_count=8, event_budget=None, min_events=6,
                              max_events=16, max_assistant_turns=3, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome",
                              consumer="post_training", target_family="llm", seed="finishes", group_size=1)
    store = DbStore(bundle.model_dump(mode="json"))
    return [TrajectoryBundle.model_validate(store.journey(entry["trajectory_id"])) for entry in sample_entries(store.entries(), count, "finishes")]


def _evaluate(judge, **options):
    messages = []
    result = evaluate_journeys(journeys=_journeys(), sector=BANKING, brief="Banking brief.", reference_quality="weak", thresholds={}, judge=judge,
                               models=["a", "b"], repeats=3, temperature=0.7, progress=lambda done, total, message: messages.append(message), **options)
    return result, messages


def _shape(result):
    return [(v["rubric"], v["judge_model"], v["trajectory_id"], v["order"], v.get("control"), v["repeat"], v["score"]) for v in result["verdicts"]]


def test_a_call_the_engine_timed_out_is_asked_again_and_the_cycle_completes():
    clean, _ = _evaluate(RecordingJudge())
    flaky = Flaky({5: [504], 9: [503]})
    result, messages = _evaluate(flaky)
    # Every verdict arrives, as if nothing had failed, and each failed call cost one more attempt.
    assert _shape(result) == _shape(clean) and result["accepted"] == clean["accepted"]
    assert flaky.attempts == len(flaky.calls) + 2
    assert sum("Asking once more." in message for message in messages) == 2
    assert any("timed out after 240 seconds. Asking once more." in message for message in messages)


def test_a_call_that_fails_again_or_is_refused_fails_the_cycle():
    with pytest.raises(JudgeUnavailable) as caught:
        _evaluate(Flaky({5: [504, 504]}))
    assert caught.value.status == 504
    # A rejected key is not a timeout, and asking again would not help.
    refused = Flaky({3: [502]})
    with pytest.raises(JudgeUnavailable):
        _evaluate(refused)
    assert refused.attempts == 3


def test_answers_are_kept_as_they_arrive_and_a_cut_off_line_is_skipped(tmp_path):
    path = tmp_path / "judging" / "cycle-1.jsonl"
    kept = Answers(path)
    kept.plan(4)
    kept.keep("one", {"score": 1.0})
    with path.open("a") as handle:
        handle.write('{"key": "two", "result": {"sco')
    again = Answers(path)
    assert again.planned == 4 and again.found == {"one": {"score": 1.0}}
    again.clear()
    assert not path.exists()


def test_a_resumed_cycle_asks_only_what_it_has_not_heard(tmp_path):
    clean, _ = _evaluate(RecordingJudge())
    kept = Answers(tmp_path / "cycle-1.jsonl")
    with pytest.raises(JudgeUnavailable):
        _evaluate(Flaky({7: [504, 504]}), kept=kept)
    assert len(kept.found) == 6 and kept.planned > 6
    rest = RecordingJudge()
    resumed, messages = _evaluate(rest, kept=Answers(tmp_path / "cycle-1.jsonl"))
    assert len(rest.calls) == kept.planned - 6
    assert _shape(resumed) == _shape(clean)
    assert messages[0] == f"Resuming: 6 of {kept.planned} answers kept from the attempt that stopped."
    # A question worded differently is a different question, and is asked.
    changed = RecordingJudge()
    evaluate_journeys(journeys=_journeys(), sector=BANKING, brief="Another brief.", reference_quality="weak", thresholds={}, judge=changed,
                      models=["a", "b"], repeats=3, temperature=0.7, kept=Answers(tmp_path / "cycle-1.jsonl"))
    assert len(changed.calls) > len(rest.calls)


def test_a_cycle_that_stopped_is_resumed_from_the_run_page(client):
    headers = _auth(client, "resume@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    run = _run(client, headers, project_id, _ready_key(client, headers), target_trajectory_count=8, event_budget=None).json()
    runtime.judge = Flaky({7: [504, 504]})
    stopped = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert stopped.status_code == 504
    after = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert after["cycle_count"] == 0 and after["judge_job"]["status"] == "failed"
    resume = after["judge_resume"]
    assert resume["heard"] == 6 and resume["planned"] > 6

    runtime.judge = rest = RecordingJudge()
    finished = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["cycle_count"] == 1 and body["judge_resume"] is None
    assert len(rest.calls) == resume["planned"] - 6
    # The cycle holds every answer, so the kept ones are gone.
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        assert not answers_path(db.get(Run, run["id"]), 1).exists()
    finally:
        db.close()


def test_the_judges_questions_leave_their_justification_uncapped():
    # A 40-word cap kept answers inside the engine's limit but changed the scores on the same journeys, so the questions
    # the judges score by carry none.
    for question in (HELPFULNESS_QUESTION, CORRECTNESS_QUESTION, SAFETY_QUESTION):
        assert "words" not in question
