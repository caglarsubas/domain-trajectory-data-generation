from trajectory_contract import TrajectoryBundle, banking_fixture
from sectors.banking.checks import banking_hard_checks
from sectors.registry import get_sector, known_sectors


def test_fixture_round_trip_and_alternative_branch():
    bundle = banking_fixture()
    restored = TrajectoryBundle.model_validate(bundle.model_dump(mode="json"))
    assert restored.trajectories[1].parent_trajectory_id == "T100"
    assert restored.trajectories[1].branch_event_id == "E04"
    assert restored.samples[0].sequences[0].contexts[0].segments[1].trainable is True
    assert banking_hard_checks(restored) == []


def test_registered_sectors_stop_at_insurance():
    assert known_sectors() == ["banking", "insurance"]
    assert get_sector("insurance").id == "insurance"
    with pytest_raises():
        get_sector("telecommunication")


def pytest_raises():
    import pytest

    return pytest.raises(ValueError)


def test_card_activation_before_issue_is_rejected():
    bundle = banking_fixture()
    for event in bundle.events:
        if event.event_id == "E09":
            event.event_time = event.event_time.replace(year=2020)
    errors = banking_hard_checks(bundle)
    assert any("card activated before issuance" in item for item in errors)


def test_disbursement_before_approval_is_rejected():
    bundle = banking_fixture()
    bundle.events[0].event_type = "loan.disbursed"
    bundle.trajectories[0].event_ids = ["E01"]
    errors = banking_hard_checks(bundle)
    assert any("loan disbursed before application approval" in item for item in errors)
