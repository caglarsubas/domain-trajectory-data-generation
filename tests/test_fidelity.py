from datetime import timedelta

import pytest

from app import runtime
from sectors.banking.corpus import steering_from_text as banking_steering
from sectors.banking.generate import generate_banking_bundle
from sectors.banking.pack import SUB_DOMAINS
from sectors.insurance.corpus import steering_from_text as insurance_steering
from sectors.journeys import UnsupportedLanguage
from test_api import _auth, _project, _ready_key, _run


def _bundle(**overrides):
    payload = {
        "sub_domains": list(SUB_DOMAINS),
        "language": "en",
        "target_trajectory_count": 32,
        "event_budget": None,
        "min_events": 6,
        "max_events": 20,
        "max_assistant_turns": 3,
        "start_mode": "cold",
        "reward_mechanism": "binary_outcome",
        "signal_mechanism": "outcome",
        "consumer": "post_training",
        "target_family": "llm",
        "seed": "fidelity",
    }
    payload.update(overrides)
    return generate_banking_bundle(**payload)


def test_terms_match_whole_words_and_currency_codes_only_in_capitals():
    found = banking_steering("Our industry and country try hard; European website; discard the branching logic.")
    assert (found.currency, found.channel, found.products, found.events) == (None, None, (), ())
    assert insurance_steering("A competitor's lifecycle homepage.").products == ()
    found = banking_steering("Mostly mobile, mobile again, sometimes a branch. Prices in TRY and lira, rarely USD.")
    assert found.channel == "mobile"
    assert found.currency == "TRY"


def test_a_negated_event_is_reported_but_not_named():
    found = banking_steering("There was no kyc.failed case. The application declined twice.")
    assert found.events == ("application.declined",)
    assert found.negated == ("kyc.failed",)


def test_quality_report_measures_complete_and_comprehensive():
    bundle = _bundle()
    quality = bundle.generation.quality
    assert quality["complete"]["passed"] is True
    assert quality["complete"]["hard_check_violations"] == 0
    assert quality["complete"]["filler_runs"] == 0
    assert quality["comprehensive"]["distinct_sequences"] >= 16
    assert set(quality["comprehensive"]["event_type_coverage"]) == set(SUB_DOMAINS)
    assert quality["representative"]["status"] == "unreferenced"
    assert quality["qualitative"]["status"] == "not_measured"
    assert bundle.generation.pack_version == "banking-pack-3"


def test_the_process_map_places_each_event_type_in_time_and_in_sequence():
    from sectors.overview import OverviewAccumulator, overview_of
    from sectors.registry import get_sector

    bundle = _bundle()
    overview = overview_of(bundle, get_sector("banking").classify)
    nodes = {node["type"]: node for node in overview["nodes"]}
    assert all(node["hours"] >= 0 and node["step"] >= 1 for node in nodes.values())
    assert nodes["product.viewed"]["hours"] == 0 and nodes["product.viewed"]["step"] == 1
    assert nodes["account.opened"]["hours"] > nodes["kyc.passed"]["hours"]
    assert all(edge["hours"] >= 0 for edge in overview["edges"])

    # A checkpoint from before timing was measured still restores, and only new journeys are timed.
    old = OverviewAccumulator(get_sector("banking").classify)
    old.restore({"journeys": 1, "variants": {}, "nodes": {"product.viewed": [1, 0.0]}, "edges": {"product.viewed|application.started": 1}})
    old.add(bundle)
    restored = {node["type"]: node for node in old.report()["nodes"]}
    assert restored["product.viewed"]["count"] == nodes["product.viewed"]["count"] + 1
    assert restored["product.viewed"]["hours"] == 0


def test_money_moves_in_a_direction_and_posts_after_authorisation():
    bundle = _bundle(sub_domains=["cards_and_payments", "consumer_credit", "deposits", "onboarding_and_kyc"])
    purchases = [event for event in bundle.events if event.event_type == "card.purchase_authorised"]
    assert purchases
    for event in purchases:
        assert (event.direction, event.amount_role) == ("debit", "purchase")
        assert event.effective_time - event.event_time >= timedelta(hours=12)
    assert all(event.direction == "credit" for event in bundle.events if event.event_type == "loan.disbursed")
    lags = {event.recorded_at - event.event_time for event in bundle.events}
    assert len(lags) > 1
    qualifiers = {link.qualifier for link in bundle.event_objects if link.qualifier}
    assert {"payment_instrument", "debited_account"} <= qualifiers


def test_journeys_start_on_a_weekday_and_hour_profile_rather_than_a_grid():
    starts = [trajectory.start for trajectory in _bundle(target_trajectory_count=64).trajectories if trajectory.parent_trajectory_id is None]
    assert max(starts) - min(starts) > timedelta(days=90)
    weekend = sum(1 for start in starts if start.weekday() >= 5)
    assert weekend < len(starts) * 0.25
    assert len({start.hour for start in starts}) > 6


def test_every_sample_links_to_a_trajectory_and_turns_end_on_sentences():
    bundle = _bundle(max_assistant_turns=4)
    trajectory_ids = {trajectory.trajectory_id for trajectory in bundle.trajectories}
    assert len(bundle.samples) == len(bundle.trajectories)
    assert {sample.trajectory_id for sample in bundle.samples} == trajectory_ids
    for sample in bundle.samples:
        segments = sample.sequences[0].contexts[0].segments
        roles = [segment.role for segment in segments]
        assert roles[:2] == ["system", "user"]
        for previous, current in zip(roles[2:], roles[3:]):
            assert previous != current
        for segment in segments:
            if segment.role == "assistant":
                assert segment.trainable and segment.text.endswith(".")
            else:
                assert not segment.trainable
    assert len({sample.prompt for sample in bundle.samples}) > 1


def test_language_changes_the_words_but_not_the_journeys():
    english = _bundle(seed="words")
    turkish = _bundle(seed="words", language="tr")
    def paths(bundle):
        events = {event.event_id: event.event_type for event in bundle.events}
        return [[events[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories]
    assert paths(english) == paths(turkish)
    assert english.samples[0].prompt != turkish.samples[0].prompt


def test_an_unsupported_language_is_refused():
    with pytest.raises(UnsupportedLanguage):
        _bundle(language="de")


def test_api_refuses_unsupported_languages_and_reports_steering_per_document(client):
    headers = _auth(client, "fidelity@example.com", "password-123")
    project_id = _project(client, headers)
    credential_id = _ready_key(client, headers)
    readable = client.post(
        f"/projects/{project_id}/corpus",
        headers=headers,
        data={"kind": "deep_search"},
        files={"upload": ("notes.md", b"Customers pay in USD on mobile. No kyc.failed was seen.", "text/markdown")},
    )
    assert readable.status_code == 200, readable.text
    pdf = client.post(
        f"/projects/{project_id}/corpus",
        headers=headers,
        data={"kind": "paper"},
        files={"upload": ("paper.pdf", b"%PDF-1.7 binary stream", "application/pdf")},
    )
    assert pdf.status_code == 200, pdf.text

    refused = _run(client, headers, project_id, credential_id, start_mode="warm", language="de")
    assert refused.status_code == 422
    assert "not supported" in refused.json()["detail"]

    created = _run(client, headers, project_id, credential_id, start_mode="warm", language="en-GB")
    assert created.status_code == 200, created.text
    generation = created.json()["generation"]
    assert generation["quality"]["complete"]["passed"] is True
    assert generation["steering"]["currency"] == "USD"
    documents = {doc["name"]: doc for doc in generation["steering"]["documents"]}
    assert documents["notes.md"]["readable"] is True
    assert documents["notes.md"]["negated_events"] == ["kyc.failed"]
    assert documents["paper.pdf"]["readable"] is False
    assert "PDF" in documents["paper.pdf"]["reason"]

    corpus = {item["name"]: item for item in client.get("/projects", headers=headers).json()["data"][0]["corpus"]}
    assert corpus["paper.pdf"]["readable"] is False
    assert corpus["notes.md"]["readable"] is True
    runtime.judge = None


def test_sectors_describe_languages_lanes_and_the_studio_cap(client):
    data = {item["id"]: item for item in client.get("/sectors").json()["data"]}
    banking = data["banking"]
    assert banking["languages"] == ["en", "tr"]
    assert banking["studio_cap"] == banking["small_run_sequences"] == 64
    assert banking["max_run_sequences"] == 100_000
    assert banking["event_kinds"]["card.purchase_authorised"] == "card"
    assert banking["event_kinds"]["loan.disbursed"] == "loan"
    assert [lane["kind"] for lane in banking["lanes"]][:3] == ["party", "application", "kyc"]
    assert set(banking["event_kinds"]) == set(banking["event_namespace"])
    assert data["insurance"]["event_kinds"]["premium.paid"] == "policy"
