from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.insurance.checks import insurance_hard_checks

SUB_DOMAINS = (
    "quoting",
    "underwriting",
    "policy_administration",
    "billing",
    "claims",
    "servicing",
    "complaints",
)

EVENT_NAMESPACE = (
    "product.viewed",
    "quote.started",
    "quote.submitted",
    "underwriting.started",
    "underwriting.referred",
    "underwriting.accepted",
    "underwriting.declined",
    "policy.bound",
    "policy.issued",
    "premium.paid",
    "claim.notified",
    "claim.assessed",
    "claim.settled",
    "claim.denied",
    "policy.renewed",
    "policy.cancelled",
    "complaint.received",
)

STATE_DIMENSIONS = ("relationship", "quote", "underwriting", "policy", "billing", "claim")


class InsurancePack:
    id = "insurance"
    label = "Insurance"
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
        scope = ", ".join(sub_domains) if sub_domains else "unspecified insurance scope"
        if cold_start:
            reference = (
                "Cold start: no warm-start corpus was supplied. "
                "Judge only against generic retail-insurance lifecycle order. The reference is weak."
            )
        else:
            excerpt = corpus_excerpt.strip() or "Warm-start documents were attached but not extracted."
            reference = f"Warm-start excerpt:\n{excerpt[:2000]}"
        return (
            f"Sector: insurance. Language: {language}. Sub-domains: {scope}.\n"
            "A representative trajectory respects object-centric insurance order: "
            "a quote before underwriting, acceptance before the policy is issued, "
            "the policy in force before a claim is notified, and assessment before settlement or denial.\n"
            f"{reference}"
        )

    def hard_checks(self, bundle: TrajectoryBundle) -> list[str]:
        return insurance_hard_checks(bundle)

    def generate(self, **kwargs) -> TrajectoryBundle:
        from sectors.insurance.generate import generate_insurance_bundle

        return generate_insurance_bundle(**kwargs)


INSURANCE = InsurancePack()
