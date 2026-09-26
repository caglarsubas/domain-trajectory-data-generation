from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.telecom.checks import telecom_hard_checks
from sectors.telecom.spec import LIFECYCLE, PACK

SUB_DOMAINS = (
    "sales_and_ordering",
    "activation_and_porting",
    "billing_and_payments",
    "plan_changes",
    "fault_management",
    "retention",
    "complaints",
)

EVENT_NAMESPACE = LIFECYCLE.namespace

STATE_DIMENSIONS = LIFECYCLE.dimensions


class TelecomPack:
    id = "telecom"
    label = "Telecommunications"
    sub_domains = SUB_DOMAINS
    event_namespace = EVENT_NAMESPACE
    state_dimensions = STATE_DIMENSIONS
    languages = ("en", "tr")
    # What the composer selects when a study in this sector starts.
    default_sub_domains = ("sales_and_ordering", "activation_and_porting", "billing_and_payments")
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
        scope = ", ".join(sub_domains) if sub_domains else "unspecified telecom scope"
        if cold_start:
            reference = (
                "Cold start: no warm-start corpus was supplied. "
                "Judge only against generic retail-telecom lifecycle order. The reference is weak."
            )
        else:
            excerpt = corpus_excerpt.strip() or "Warm-start documents were attached but not extracted."
            # The caller chooses the passages and keeps them within the judge's budget.
            reference = f"Warm-start reference passages:\n{excerpt}"
        _, rules = profile.rules_for(self.id)
        return (
            f"Sector: telecommunications. Language: {language}. Sub-domains: {scope}.\n"
            "A representative trajectory respects object-centric telecom order: "
            "an order and its credit check before approval, the SIM sent before the line goes live, "
            "a number port settled before activation, a bill issued before it is paid or goes overdue, "
            "a line suspended only for an overdue bill and restored only once it is paid, "
            "and a fault diagnosed before it is fixed remotely or by an engineer.\n"
            f"Jurisdiction: {profile.label}"
            + (f", amounts in {profile.currency}" if profile.currency else "")
            + f". Rules: {' '.join(rules)}\n"
            f"{reference}"
        )

    def classify(self, types: list[str]) -> str:
        from sectors.telecom.spec import classify

        return classify(types)

    def steering(self, text: str):
        from sectors.telecom.corpus import steering_from_text

        return steering_from_text(text)

    @property
    def vocabulary(self):
        from sectors.telecom.corpus import VOCABULARY

        return VOCABULARY

    def hard_checks(self, bundle: TrajectoryBundle) -> list[str]:
        return telecom_hard_checks(bundle)

    def generate(self, **kwargs) -> TrajectoryBundle:
        from sectors.telecom.generate import generate_telecom_bundle

        return generate_telecom_bundle(**kwargs)


TELECOM = TelecomPack()
