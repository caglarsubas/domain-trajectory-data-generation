import json

from sectors.jurisdictions import get_jurisdiction
from sectors.registry import get_sector
from test_api import _auth

TELECOM = get_sector("telecom")


def _bundle(**overrides):
    settings = dict(
        sub_domains=list(TELECOM.sub_domains), language="en", target_trajectory_count=40, event_budget=None, min_events=6, max_events=24,
        max_assistant_turns=4, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training",
        target_family="llm", seed="telecom-test", group_size=2,
    )
    settings.update(overrides)
    return TELECOM.generate(**settings)


def _paths(bundle):
    events = {event.event_id: event.event_type for event in bundle.events}
    return [[events[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories]


def test_telecom_journeys_follow_ordering_activation_billing_and_repair_order():
    bundle = _bundle()
    assert TELECOM.hard_checks(bundle) == []
    for path in _paths(bundle):
        if "order.approved" in path:
            assert path.index("credit_check.started") < path.index("order.approved")
            assert path[path.index("order.approved") - 1] in {"credit_check.passed", "credit_check.deposit_required"}
        if "service.activated" in path:
            assert path.index("sim.dispatched") < path.index("service.activated")
            if "port.requested" in path:
                # A requested port settles before the line goes live.
                settled = next(index for index, name in enumerate(path) if name in {"port.completed", "port.failed"})
                assert path.index("port.requested") < settled < path.index("service.activated")
        for index, name in enumerate(path):
            if name == "service.suspended":
                assert path[:index].count("bill.overdue") >= 1
            if name == "service.restored":
                assert path[:index][::-1].index("bill.paid") < path[:index][::-1].index("service.suspended")
            if name in {"fault.resolved_remotely", "engineer.dispatched"}:
                assert path[index - 1] == "fault.diagnosed" or "fault.diagnosed" in path[:index]
    kinds = {obj.object_type for obj in bundle.objects}
    assert {"product_order", "subscription", "trouble_ticket"} <= kinds


def test_a_suspension_before_an_overdue_bill_is_rejected():
    bundle = _bundle(sub_domains=["billing_and_payments"], target_trajectory_count=60, seed="arrears")
    events = {event.event_id: event for event in bundle.events}
    trajectory = next(item for item in bundle.trajectories if any(events[event_id].event_type == "service.suspended" for event_id in item.event_ids))
    suspended = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "service.suspended")
    overdue = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "bill.overdue")
    suspended.event_time, overdue.event_time = overdue.event_time, suspended.event_time
    assert any("service suspended without an overdue bill" in item for item in TELECOM.hard_checks(bundle))


def test_turkish_telecom_names_the_line_and_its_local_plan():
    bundle = _bundle(language="tr", jurisdiction="tr", target_trajectory_count=6, seed="tr")
    assert any("telekom" in sample.prompt.lower() for sample in bundle.samples)
    subscription = next(obj for obj in bundle.objects if obj.object_type == "subscription")
    assert subscription.attributes["product_name"] == "faturalı hat"
    system = bundle.samples[0].sequences[0].contexts[0].segments[0].text
    assert "Rules:" in system and "numara taşıma" in system and "KYC:" not in system


def test_telecom_episodes_use_tm_forum_operations_and_telecom_wording():
    bundle = _bundle(episodes=True, group_size=4, target_trajectory_count=16, seed="episodes")
    assert bundle.episodes
    names = {tool.name for episode in bundle.episodes for tool in episode.tools}
    assert names & {"CreditManagement.Evaluate", "ProductOrdering.Update", "NumberPortability.Execute", "Payment.Execute", "TroubleTicket.Execute"}
    assert "telecommunications provider" in bundle.episodes[0].rollouts[0].turns[0].text


def test_the_judge_brief_and_jurisdictions_describe_telecom_rules():
    brief = TELECOM.judge_brief(sub_domains=["activation_and_porting"], language="en", corpus_excerpt="", cold_start=True, jurisdiction="uk")
    assert "number port settled before activation" in brief and "switch provider by text" in brief and "KYC" not in brief
    assert get_jurisdiction("uk").rules_for("banking")[0] == "KYC" and get_jurisdiction("uk").rules_for("telecom")[0] == "Rules"


def test_a_telecom_study_runs_and_exports_through_the_api(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "telecom-api@example.com", "password-123")
    sectors = {item["id"]: item for item in client.get("/sectors").json()["data"]}
    assert sectors["telecom"]["label"] == "Telecommunications"
    assert sectors["telecom"]["default_sub_domains"] == ["sales_and_ordering", "activation_and_porting", "billing_and_payments"]
    assert client.post("/projects", headers=headers, json={"name": "Nowhere", "sector": "shipping"}).status_code == 422
    project = client.post("/projects", headers=headers, json={"name": "Mobile onboarding", "sector": "telecom"}).json()
    run = client.post("/runs", headers=headers, json={
        "project_id": project["id"], "sector": "telecom", "target_trajectory_count": 8, "event_budget": None, "min_events": 6, "max_events": 20,
        "max_assistant_turns": 4, "sub_domains": ["sales_and_ordering", "activation_and_porting", "billing_and_payments"], "language": "en",
        "start_mode": "cold", "cold_start_acknowledged": True, "reward_mechanism": "binary_outcome", "signal_mechanism": "outcome",
        "consumer": "evaluation", "target_family": "llm", "max_cycles": 1, "group_size": 4,
    })
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "generated" and body["bundle"]["episodes"]
    tasks = client.get(f"/runs/{body['id']}/export/tasks.jsonl", headers=headers, params={"allow_unaccepted": "true"}).text.splitlines()
    report = json.loads(client.get(f"/runs/{body['id']}/export/evaluation.json", headers=headers, params={"allow_unaccepted": "true"}).text)
    assert len(tasks) == report["tasks"]["journey"] + report["tasks"]["agent_episode"] and report["environment"]["sector"] == "telecom"
