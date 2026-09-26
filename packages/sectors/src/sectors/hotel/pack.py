from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.hotel.checks import hotel_hard_checks
from sectors.hotel.spec import LIFECYCLE, PACK

SUB_DOMAINS = (
    "booking_and_reservations",
    "modifications_and_cancellations",
    "arrival_and_check_in",
    "in_stay_services",
    "check_out_and_billing",
    "loyalty_and_reviews",
    "complaints",
)

EVENT_NAMESPACE = LIFECYCLE.namespace

STATE_DIMENSIONS = LIFECYCLE.dimensions


class HotelPack:
    id = "hotel"
    label = "Hotel"
    sub_domains = SUB_DOMAINS
    event_namespace = EVENT_NAMESPACE
    state_dimensions = STATE_DIMENSIONS
    languages = ("en", "tr")
    # What the composer selects when a study in this sector starts.
    default_sub_domains = ("booking_and_reservations", "arrival_and_check_in", "check_out_and_billing")
    lifecycle = LIFECYCLE
    # The pack's full spec: its goal, phrases, and version, which exports describe.
    pack = PACK

    def judge_brief(
        self,
        *,
        sub_domains: list[str],
        language: str,
        corpus_excerpt: str,
        cold_start: bool,
        jurisdiction: str = "neutral",
    ) -> str:
        from sectors.jurisdictions import get_jurisdiction

        profile = get_jurisdiction(jurisdiction)
        scope = ", ".join(sub_domains) if sub_domains else "unspecified hotel scope"
        if cold_start:
            reference = (
                "Cold start: no warm-start corpus was supplied. "
                "Judge only against generic hotel guest lifecycle order. The reference is weak."
            )
        else:
            excerpt = corpus_excerpt.strip() or "Warm-start documents were attached but not extracted."
            # The caller chooses the passages and keeps them within the judge's budget.
            reference = f"Warm-start reference passages:\n{excerpt}"
        _, rules = profile.rules_for(self.id)
        return (
            f"Sector: hotel. Language: {language}. Sub-domains: {scope}.\n"
            "A representative trajectory respects object-centric hotel order: "
            "a reservation guaranteed before it is confirmed, a room assigned before check-in, "
            "changes and cancellations only before arrival, services and charges only during the stay, "
            "the folio settled or disputed after check-out, and a review only once the stay has ended.\n"
            f"Jurisdiction: {profile.label}"
            + (f", amounts in {profile.currency}" if profile.currency else "")
            + f". Rules: {' '.join(rules)}\n"
            f"{reference}"
        )

    def classify(self, types: list[str]) -> str:
        from sectors.hotel.spec import classify

        return classify(types)

    def steering(self, text: str):
        from sectors.hotel.corpus import steering_from_text

        return steering_from_text(text)

    @property
    def vocabulary(self):
        from sectors.hotel.corpus import VOCABULARY

        return VOCABULARY

    def hard_checks(self, bundle: TrajectoryBundle) -> list[str]:
        return hotel_hard_checks(bundle)

    def generate(self, **kwargs) -> TrajectoryBundle:
        from sectors.hotel.generate import generate_hotel_bundle

        return generate_hotel_bundle(**kwargs)


HOTEL = HotelPack()
