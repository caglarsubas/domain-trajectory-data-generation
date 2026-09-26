"""A labeled synthetic banking journey used before generation exists."""

from datetime import datetime, timezone

from trajectory_contract.models import (
    Context,
    Event,
    EventObject,
    ObjectRecord,
    ObservationStatus,
    Relationship,
    Sample,
    Segment,
    Sequence,
    StateTransition,
    Trajectory,
    TrajectoryBundle,
)


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def banking_fixture() -> TrajectoryBundle:
    """One current-account journey plus a KYC-review alternative.

    Every record is synthetic. Approval precedes account opening, and card issuance precedes activation. No loan is disbursed.
    """

    objects = [
        ObjectRecord(object_id="P001", object_type="party", subtype="individual", pii_class="synthetic"),
        ObjectRecord(object_id="OFFER01", object_type="product_offering", subtype="current_account"),
        ObjectRecord(object_id="APP01", object_type="application", subtype="current_account"),
        ObjectRecord(object_id="KYC01", object_type="kyc_case", subtype="onboarding"),
        ObjectRecord(object_id="ACC01", object_type="account", subtype="current"),
        ObjectRecord(object_id="CARD01", object_type="card", subtype="debit"),
    ]
    relationships = [
        Relationship(relationship_id="R1", subject_id="P001", predicate="APPLIED_FOR", object_id="APP01", source="fixture"),
        Relationship(relationship_id="R2", subject_id="APP01", predicate="RESULTED_IN", object_id="ACC01", source="fixture"),
        Relationship(relationship_id="R3", subject_id="CARD01", predicate="LINKED_TO", object_id="ACC01", source="fixture"),
    ]

    def ev(event_id: str, event_type: str, when: str, **kwargs: object) -> Event:
        return Event(
            event_id=event_id,
            event_type=event_type,
            event_time=_t(when),
            effective_time=_t(when),
            recorded_at=_t(when),
            observation_status=ObservationStatus.simulated,
            **kwargs,  # type: ignore[arg-type]
        )

    events = [
        ev("E01", "product.viewed", "2026-01-03T09:00:00", session_id="S01", channel_id="web"),
        ev("E02", "application.started", "2026-01-03T09:04:00", session_id="S01", case_id="C01", channel_id="web"),
        ev("E03", "application.submitted", "2026-01-03T09:13:00", session_id="S01", case_id="C01", channel_id="web"),
        ev("E04", "kyc.started", "2026-01-03T09:15:00", session_id="S01", case_id="C01", channel_id="web"),
        ev("E05", "kyc.passed", "2026-01-03T09:16:00", session_id="S01", case_id="C01", channel_id="system"),
        ev("E05A", "application.approved", "2026-01-03T09:16:30", case_id="C01", channel_id="system"),
        ev("E06", "account.opened", "2026-01-03T09:17:00", case_id="C01", channel_id="system"),
        ev("E07", "account.funded", "2026-01-04T18:20:00", session_id="S02", case_id="C01", channel_id="mobile", amount=1250, currency="GBP"),
        ev("E08", "card.issued", "2026-01-06T11:00:00", case_id="C01", channel_id="system"),
        ev("E09", "card.activated", "2026-01-08T19:12:00", session_id="S03", case_id="C01", channel_id="mobile"),
        ev("E10", "card.purchase_authorised", "2026-01-08T20:03:00", case_id="C01", channel_id="pos", amount=26.4, currency="GBP"),
        ev("A05", "kyc.review_required", "2026-01-03T09:16:00", case_id="C01", channel_id="system"),
        ev("A06", "kyc.document_submitted", "2026-01-04T12:00:00", case_id="C01", channel_id="mobile"),
        ev("A07", "kyc.passed", "2026-01-05T15:30:00", case_id="C01", channel_id="back_office"),
    ]
    links = [
        EventObject(event_id="E01", object_id="P001", object_role="prospect"),
        EventObject(event_id="E01", object_id="OFFER01", object_role="offering"),
        EventObject(event_id="E02", object_id="P001", object_role="applicant"),
        EventObject(event_id="E02", object_id="APP01", object_role="application"),
        EventObject(event_id="E03", object_id="APP01", object_role="application"),
        EventObject(event_id="E04", object_id="P001", object_role="subject"),
        EventObject(event_id="E04", object_id="KYC01", object_role="case"),
        EventObject(event_id="E05", object_id="P001", object_role="subject"),
        EventObject(event_id="E05", object_id="KYC01", object_role="case"),
        EventObject(event_id="E05A", object_id="P001", object_role="applicant"),
        EventObject(event_id="E05A", object_id="APP01", object_role="application"),
        EventObject(event_id="E06", object_id="P001", object_role="holder"),
        EventObject(event_id="E06", object_id="ACC01", object_role="account"),
        EventObject(event_id="E07", object_id="ACC01", object_role="account"),
        EventObject(event_id="E08", object_id="ACC01", object_role="account"),
        EventObject(event_id="E08", object_id="CARD01", object_role="card"),
        EventObject(event_id="E09", object_id="CARD01", object_role="card"),
        EventObject(event_id="E10", object_id="CARD01", object_role="card"),
        EventObject(event_id="E10", object_id="ACC01", object_role="account"),
        EventObject(event_id="A05", object_id="P001", object_role="subject"),
        EventObject(event_id="A05", object_id="KYC01", object_role="case"),
        EventObject(event_id="A06", object_id="KYC01", object_role="case"),
        EventObject(event_id="A07", object_id="P001", object_role="subject"),
        EventObject(event_id="A07", object_id="KYC01", object_role="case"),
    ]
    transitions = [
        StateTransition(event_id="E02", object_id="APP01", state_dimension="application", state_before=None, state_after="started"),
        StateTransition(event_id="E03", object_id="APP01", state_dimension="application", state_before="started", state_after="submitted"),
        StateTransition(event_id="E04", object_id="KYC01", state_dimension="kyc", state_before=None, state_after="pending"),
        StateTransition(event_id="E05", object_id="KYC01", state_dimension="kyc", state_before="pending", state_after="verified"),
        StateTransition(event_id="E05A", object_id="APP01", state_dimension="application", state_before="submitted", state_after="approved"),
        StateTransition(event_id="E06", object_id="ACC01", state_dimension="account", state_before="pending", state_after="active"),
        StateTransition(event_id="E08", object_id="CARD01", state_dimension="card", state_before=None, state_after="issued"),
        StateTransition(event_id="E09", object_id="CARD01", state_dimension="card", state_before="issued", state_after="active"),
        StateTransition(event_id="A05", object_id="KYC01", state_dimension="kyc", state_before="pending", state_after="review_required"),
        StateTransition(event_id="A07", object_id="KYC01", state_dimension="kyc", state_before="review_required", state_after="verified"),
    ]
    trajectories = [
        Trajectory(
            trajectory_id="T100",
            root_party_id="P001",
            trajectory_type="acquisition_to_first_purchase",
            start=_t("2026-01-03T09:00:00"),
            end=_t("2026-01-08T20:03:00"),
            observed_or_synthetic="synthetic",
            generator_id="fixture",
            probability=1.0,
            event_ids=["E01", "E02", "E03", "E04", "E05", "E05A", "E06", "E07", "E08", "E09", "E10"],
        ),
        Trajectory(
            trajectory_id="T100-A",
            root_party_id="P001",
            trajectory_type="acquisition_to_first_purchase",
            start=_t("2026-01-03T09:00:00"),
            end=_t("2026-01-05T15:30:00"),
            observed_or_synthetic="alternative",
            parent_trajectory_id="T100",
            branch_event_id="E04",
            generator_id="fixture",
            probability=0.22,
            event_ids=["E01", "E02", "E03", "E04", "A05", "A06", "A07"],
        ),
    ]
    samples = [
        Sample(
            sample_id="SAMP-T100",
            prompt="Open a current account and reach a first card purchase.",
            sequences=[
                Sequence(
                    sequence_id="SEQ-T100",
                    reward=None,
                    advantage=None,
                    contexts=[
                        Context(
                            context_id="CTX-T100",
                            segments=[
                                Segment(segment_id="SEG-1", role="user", text="I want a current account."),
                                Segment(
                                    segment_id="SEG-2",
                                    role="assistant",
                                    text="Application submitted, KYC passed, application approved, account opened, card issued then activated.",
                                    trainable=True,
                                ),
                            ],
                        )
                    ],
                )
            ],
        )
    ]
    return TrajectoryBundle(
        objects=objects,
        relationships=relationships,
        events=events,
        event_objects=links,
        state_transitions=transitions,
        trajectories=trajectories,
        samples=samples,
    )
