import json

from sectors.jurisdictions import get_jurisdiction
from sectors.registry import get_sector
from test_api import _auth

HOTEL = get_sector("hotel")


def _bundle(**overrides):
    settings = dict(
        sub_domains=list(HOTEL.sub_domains), language="en", target_trajectory_count=60, event_budget=None, min_events=6, max_events=24,
        max_assistant_turns=4, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training",
        target_family="llm", seed="hotel-test", group_size=2,
    )
    settings.update(overrides)
    return HOTEL.generate(**settings)


def _paths(bundle):
    events = {event.event_id: event.event_type for event in bundle.events}
    return [[events[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories]


def test_hotel_journeys_guarantee_before_confirming_and_bill_after_check_out():
    bundle = _bundle()
    assert HOTEL.hard_checks(bundle) == []
    in_stay = {"service.requested", "issue.reported", "charge.posted", "room.moved"}
    for path in _paths(bundle):
        if "reservation.confirmed" in path:
            assert path.index("guarantee.accepted") < path.index("reservation.confirmed")
        if "guest.checked_in" in path:
            arrived = path.index("guest.checked_in")
            assert "room.assigned" in path[:arrived]
            # Changes and cancellations happen only before the room is assigned.
            assert not {"modification.requested", "reservation.cancelled"} & set(path[path.index("room.assigned"):])
            departed = path.index("guest.checked_out") if "guest.checked_out" in path else len(path)
            assert all(arrived < index < departed for index, name in enumerate(path) if name in in_stay)
        if "folio.settled" in path or "folio.disputed" in path:
            settled = next(index for index, name in enumerate(path) if name in {"folio.settled", "folio.disputed"})
            assert path.index("guest.checked_out") < settled
        for name in ("review.positive", "review.negative"):
            if name in path:
                assert {"guest.checked_out", "guest.walked"} & set(path[: path.index(name)])
    kinds = {obj.object_type for obj in bundle.objects}
    assert {"reservation", "stay", "rate_plan"} <= kinds


def test_an_oversold_house_walks_a_guest_and_compensates_them():
    bundle = _bundle(sub_domains=["arrival_and_check_in"], target_trajectory_count=200, group_size=4, seed="walked", materialization_cap=800)
    walked = [path for path in _paths(bundle) if "guest.walked" in path]
    assert walked and any("relocation.compensated" in path for path in walked)
    assert all("guest.checked_in" not in path for path in walked)


def test_a_folio_settled_before_check_out_is_rejected():
    bundle = _bundle(sub_domains=["check_out_and_billing"], target_trajectory_count=20, seed="settle")
    events = {event.event_id: event for event in bundle.events}
    trajectory = next(item for item in bundle.trajectories if any(events[event_id].event_type == "folio.settled" for event_id in item.event_ids))
    settled = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "folio.settled")
    checked_in = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "guest.checked_in")
    settled.event_time = checked_in.event_time
    assert any("folio settled before check-out" in item for item in HOTEL.hard_checks(bundle))


def test_turkish_hotel_names_the_rate_and_the_guest_registration_rule():
    bundle = _bundle(language="tr", jurisdiction="tr", target_trajectory_count=6, seed="tr")
    assert any("otel" in sample.prompt.lower() for sample in bundle.samples)
    rate = next(obj for obj in bundle.objects if obj.object_type == "rate_plan")
    assert rate.attributes["product_name"] == "esnek fiyat"
    system = bundle.samples[0].sequences[0].contexts[0].segments[0].text
    assert "Rules:" in system and "Kimlik Bildirim Sistemi" in system and "KYC:" not in system


def test_hotel_episodes_use_htng_style_operations_and_hotel_wording():
    bundle = _bundle(episodes=True, group_size=4, target_trajectory_count=16, seed="episodes")
    assert bundle.episodes
    names = {tool.name for episode in bundle.episodes for tool in episode.tools}
    assert names & {"PaymentGuarantee.Evaluate", "FrontOffice.Evaluate", "Folio.Execute", "ServiceRequest.Execute"}
    assert "at a hotel" in bundle.episodes[0].rollouts[0].turns[0].text


def test_the_judge_brief_describes_hotel_order_and_uk_guest_records():
    brief = HOTEL.judge_brief(sub_domains=["arrival_and_check_in"], language="en", corpus_excerpt="", cold_start=True, jurisdiction="uk")
    assert "a room assigned before check-in" in brief and "aged 16 or over" in brief and "KYC" not in brief
    assert get_jurisdiction("uk").rules_for("hotel")[0] == "Rules"


def test_a_hotel_study_runs_and_exports_evaluation_tasks_through_the_api(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "hotel-api@example.com", "password-123")
    sectors = {item["id"]: item for item in client.get("/sectors").json()["data"]}
    assert sectors["hotel"]["default_sub_domains"] == ["booking_and_reservations", "arrival_and_check_in", "check_out_and_billing"]
    project = client.post("/projects", headers=headers, json={"name": "City stays", "sector": "hotel"}).json()
    run = client.post("/runs", headers=headers, json={
        "project_id": project["id"], "sector": "hotel", "target_trajectory_count": 8, "event_budget": None, "min_events": 6, "max_events": 20,
        "max_assistant_turns": 4, "sub_domains": ["booking_and_reservations", "arrival_and_check_in", "check_out_and_billing"], "language": "en",
        "start_mode": "cold", "cold_start_acknowledged": True, "reward_mechanism": "binary_outcome", "signal_mechanism": "solution_rubric",
        "consumer": "evaluation", "target_family": "llm", "max_cycles": 1, "group_size": 4,
    })
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "generated" and body["generation"]["rewards"]["signal"] == "solution_rubric"
    report = json.loads(client.get(f"/runs/{body['id']}/export/evaluation.json", headers=headers, params={"allow_unaccepted": "true"}).text)
    assert report["environment"]["sector"] == "hotel" and report["tasks"]["journey"] == len(body["bundle"]["samples"])
