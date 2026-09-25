from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.banking.checks import banking_hard_checks
from sectors.banking.spec import LIFECYCLE

SUB_DOMAINS = (
    "deposits",
    "cards_and_payments",
    "consumer_credit",
    "onboarding_and_kyc",
    "servicing",
    "complaints",
    "risk_and_compliance",
)

EVENT_NAMESPACE = LIFECYCLE.namespace

STATE_DIMENSIONS = LIFECYCLE.dimensions


class BankingPack:
    id = "banking"
    label = "Banking"
    sub_domains = SUB_DOMAINS
    event_namespace = EVENT_NAMESPACE
    state_dimensions = STATE_DIMENSIONS
    languages = ("en", "tr")

    def judge_brief(
        self,
        *,
        sub_domains: list[str],
        language: str,
        corpus_excerpt: str,
        cold_start: bool,
    ) -> str:
        scope = ", ".join(sub_domains) if sub_domains else "unspecified banking scope"
        if cold_start:
            reference = (
                "Cold start: no warm-start corpus was supplied. "
                "Judge only against generic retail-banking lifecycle order. The reference is weak."
            )
        else:
            excerpt = corpus_excerpt.strip() or "Warm-start documents were attached but not extracted."
            reference = f"Warm-start excerpt:\n{excerpt[:2000]}"
        return (
            f"Sector: banking. Language: {language}. Sub-domains: {scope}.\n"
            "A representative trajectory respects object-centric banking order: "
            "an application with verified KYC before account opening, one decision per application, "
            "card issuance before activation, no loan disbursement before approval, "
            "and nothing on an account after it closes.\n"
            f"{reference}"
        )

    def steering(self, text: str):
        from sectors.banking.corpus import steering_from_text

        return steering_from_text(text)

    def hard_checks(self, bundle: TrajectoryBundle) -> list[str]:
        return banking_hard_checks(bundle)

    def generate(self, **kwargs) -> TrajectoryBundle:
        from sectors.banking.generate import generate_banking_bundle

        return generate_banking_bundle(**kwargs)


BANKING = BankingPack()
