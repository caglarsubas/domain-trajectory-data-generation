from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.airline.checks import airline_hard_checks
from sectors.airline.spec import LIFECYCLE, PACK

SUB_DOMAINS = (
    "shopping_and_booking",
    "ancillaries_and_changes",
    "check_in_and_boarding",
    "disruption_and_compensation",
    "baggage",
    "loyalty",
    "complaints",
)

EVENT_NAMESPACE = LIFECYCLE.namespace

STATE_DIMENSIONS = LIFECYCLE.dimensions


class AirlinePack:
    id = "airline"
    label = "Airline"
    sub_domains = SUB_DOMAINS
    event_namespace = EVENT_NAMESPACE
    state_dimensions = STATE_DIMENSIONS
    languages = ("en", "tr")
    # What the composer selects when a study in this sector starts.
    default_sub_domains = ("shopping_and_booking", "check_in_and_boarding", "disruption_and_compensation")
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
        scope = ", ".join(sub_domains) if sub_domains else "unspecified airline scope"
        if cold_start:
            reference = (
                "Cold start: no warm-start corpus was supplied. "
                "Judge only against generic airline passenger lifecycle order. The reference is weak."
            )
        else:
            excerpt = corpus_excerpt.strip() or "Warm-start documents were attached but not extracted."
            # The caller chooses the passages and keeps them within the judge's budget.
            reference = f"Warm-start reference passages:\n{excerpt}"
        _, rules = profile.rules_for(self.id)
        return (
            f"Sector: airline. Language: {language}. Sub-domains: {scope}.\n"
            "A representative trajectory respects object-centric airline order: "
            "an order paid before it is ticketed, check-in before boarding, a bag dropped after check-in and before boarding, "
            "departure only after the passenger boarded and arrival after departure, "
            "a cancelled flight or denied boarding followed by a rebooking or a refund, "
            "and compensation claimed only after a late arrival, a cancellation, or denied boarding.\n"
            f"Jurisdiction: {profile.label}"
            + (f", amounts in {profile.currency}" if profile.currency else "")
            + f". Rules: {' '.join(rules)}\n"
            f"{reference}"
        )

    def classify(self, types: list[str]) -> str:
        from sectors.airline.spec import classify

        return classify(types)

    def steering(self, text: str):
        from sectors.airline.corpus import steering_from_text

        return steering_from_text(text)

    @property
    def vocabulary(self):
        from sectors.airline.corpus import VOCABULARY

        return VOCABULARY

    def hard_checks(self, bundle: TrajectoryBundle) -> list[str]:
        return airline_hard_checks(bundle)

    def generate(self, **kwargs) -> TrajectoryBundle:
        from sectors.airline.generate import generate_airline_bundle

        return generate_airline_bundle(**kwargs)


AIRLINE = AirlinePack()
