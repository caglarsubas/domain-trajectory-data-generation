import json

from sectors.jurisdictions import get_jurisdiction
from sectors.registry import get_sector
from test_api import _auth

AIRLINE = get_sector("airline")


def _bundle(**overrides):
    settings = dict(
        sub_domains=list(AIRLINE.sub_domains), language="en", target_trajectory_count=60, event_budget=None, min_events=6, max_events=24,
        max_assistant_turns=4, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training",
        target_family="llm", seed="airline-test", group_size=2,
    )
    settings.update(overrides)
    return AIRLINE.generate(**settings)


def _paths(bundle):
    events = {event.event_id: event.event_type for event in bundle.events}
    return [[events[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories]


def test_airline_journeys_pay_before_ticketing_and_board_before_flying():
    bundle = _bundle()
    assert AIRLINE.hard_checks(bundle) == []
    for path in _paths(bundle):
        if "order.confirmed" in path:
            assert path.index("payment.captured") < path.index("order.confirmed")
        if "passenger.boarded" in path:
            boarded = path.index("passenger.boarded")
            assert "passenger.checked_in" in path[:boarded]
            # A checked bag is in the hold before its passenger boards.
            if "bag.added" in path[:boarded]:
                assert "bag.dropped" in path[:boarded]
        if "flight.departed" in path:
            assert path.index("passenger.boarded") < path.index("flight.departed")
        if "compensation.claimed" in path:
            claimed = path.index("compensation.claimed")
            assert {"flight.arrived_late", "flight.cancelled", "boarding.denied"} & set(path[:claimed])
        for index, name in enumerate(path):
            if name == "order.rebooked":
                assert {"flight.cancelled", "boarding.denied"} & set(path[:index])
    kinds = {obj.object_type for obj in bundle.objects}
    assert {"order", "flight", "fare_offer"} <= kinds


def test_disruptions_lead_to_a_rebooking_or_a_refund_and_a_claim_can_follow():
    bundle = _bundle(sub_domains=["disruption_and_compensation", "check_in_and_boarding"], target_trajectory_count=120, group_size=4, seed="disruption")
    paths = _paths(bundle)
    assert any("order.rebooked" in path for path in paths) and any("compensation.paid" in path for path in paths)
    assert any("flight.arrived_late" in path for path in paths)
    for path in paths:
        if "flight.arrived_late" in path:
            assert "flight.delayed" in path[: path.index("flight.arrived_late")]


def test_a_claim_before_any_disruption_is_rejected():
    bundle = _bundle(sub_domains=["disruption_and_compensation"], target_trajectory_count=120, group_size=4, seed="claims")
    events = {event.event_id: event for event in bundle.events}
    trajectory = next(item for item in bundle.trajectories if any(events[event_id].event_type == "compensation.claimed" for event_id in item.event_ids))
    claimed = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "compensation.claimed")
    confirmed = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "order.confirmed")
    claimed.event_time = confirmed.event_time
    assert any("compensation claimed without a delay, cancellation, or denied boarding" in item for item in AIRLINE.hard_checks(bundle))


def test_turkish_airline_names_the_fare_and_the_passenger_rights_rules():
    bundle = _bundle(language="tr", jurisdiction="tr", target_trajectory_count=6, seed="tr")
    assert any("havayolu" in sample.prompt.lower() for sample in bundle.samples)
    offer = next(obj for obj in bundle.objects if obj.object_type == "fare_offer")
    assert offer.attributes["product_name"] == "ekonomi sınıfı bilet"
    system = bundle.samples[0].sequences[0].contexts[0].segments[0].text
    assert "Rules:" in system and "SHY-Yolcu" in system and "KYC:" not in system


def test_airline_episodes_use_ndc_style_operations_and_airline_wording():
    bundle = _bundle(episodes=True, group_size=4, target_trajectory_count=16, seed="episodes")
    assert bundle.episodes
    names = {tool.name for episode in bundle.episodes for tool in episode.tools}
    assert names & {"Payment.Execute", "DepartureControl.Evaluate", "OrderManagement.Execute", "FlightOperations.Capture"}
    assert "an airline" in bundle.episodes[0].rollouts[0].turns[0].text


def test_the_judge_brief_describes_airline_order_and_uk261():
    brief = AIRLINE.judge_brief(sub_domains=["disruption_and_compensation"], language="en", corpus_excerpt="", cold_start=True, jurisdiction="uk")
    assert "check-in before boarding" in brief and "UK261" in brief and "KYC" not in brief
    assert get_jurisdiction("uk").rules_for("airline")[0] == "Rules"


def test_an_airline_study_runs_through_the_api(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "airline-api@example.com", "password-123")
    sectors = {item["id"]: item for item in client.get("/sectors").json()["data"]}
    assert sectors["airline"]["default_sub_domains"] == ["shopping_and_booking", "check_in_and_boarding", "disruption_and_compensation"]
    project = client.post("/projects", headers=headers, json={"name": "Summer travel", "sector": "airline"}).json()
    run = client.post("/runs", headers=headers, json={
        "project_id": project["id"], "sector": "airline", "target_trajectory_count": 8, "event_budget": None, "min_events": 6, "max_events": 20,
        "max_assistant_turns": 4, "sub_domains": ["shopping_and_booking", "check_in_and_boarding", "disruption_and_compensation"], "language": "en",
        "start_mode": "cold", "cold_start_acknowledged": True, "reward_mechanism": "binary_outcome", "signal_mechanism": "outcome",
        "consumer": "decision_scoring", "target_family": "jev", "max_cycles": 1, "group_size": 2,
    })
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "generated" and body["bundle"]["decisions"]
    lines = client.get(f"/runs/{body['id']}/export/decisions.jsonl", headers=headers, params={"allow_unaccepted": "true"}).text.splitlines()
    assert lines and json.loads(lines[0])["schema_version"] == "decision-record/1"
