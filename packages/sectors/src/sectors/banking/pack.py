from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.banking.checks import banking_hard_checks

SUB_DOMAINS = (
    "deposits",
    "cards_and_payments",
    "consumer_credit",
    "onboarding_and_kyc",
    "servicing",
    "complaints",
    "risk_and_compliance",
)

EVENT_NAMESPACE = (
    "product.viewed",
    "application.started",
    "application.submitted",
    "application.approved",
    "application.declined",
    "kyc.started",
    "kyc.document_submitted",
    "kyc.review_required",
    "kyc.passed",
    "kyc.failed",
    "account.opened",
    "account.funded",
    "card.issued",
    "card.activated",
    "card.purchase_authorised",
    "loan.disbursed",
    "loan.delinquent",
    "complaint.received",
)

STATE_DIMENSIONS = ("relationship", "kyc", "application", "account", "credit", "card")


class BankingPack:
    id = "banking"
    label = "Banking"
    sub_domains = SUB_DOMAINS
    event_namespace = EVENT_NAMESPACE
    state_dimensions = STATE_DIMENSIONS

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
            "application before account opening, KYC before activation, card issuance before card activation, "
            "and no loan disbursement before approval.\n"
            f"{reference}"
        )

    def hard_checks(self, bundle: TrajectoryBundle) -> list[str]:
        return banking_hard_checks(bundle)


BANKING = BankingPack()
