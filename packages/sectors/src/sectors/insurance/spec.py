"""Retail insurance as orthogonal state machines.

Dimensions: relationship on the party, quote and underwriting on the quote,
policy and billing on the policy, the claim, and the complaint case. Weights
are hand-set priors until data sources calibrate them.
"""

from __future__ import annotations

from typing import Any

from sectors.journeys import PackSpec
from sectors.lifecycle import EventSpec, LifecycleSpec, need, put

GENERATOR_ID = "insurance-semi-markov-v2"

QU = "quoting"
UW = "underwriting"
PA = "policy_administration"
BI = "billing"
CL = "claims"
SV = "servicing"
CO = "complaints"

DAY = 24.0
IN_FORCE = ("in_force", "renewed")

LIFECYCLE = LifecycleSpec(
    object_types={
        "party": "party",
        "offering": "product_offering",
        "quote": "quote",
        "policy": "policy",
        "claim": "claim",
        "complaint": "complaint",
    },
    events=(
        EventSpec(
            "product.viewed", (QU,),
            requires=(need("party", "relationship", None),),
            sets=(put("party", "relationship", "prospect"),),
            dwell_hours=(0.0, 0.0), opening=True,
        ),
        EventSpec(
            "quote.started", (QU,),
            requires=(need("party", "relationship", "prospect"), need("quote", "quote", None)),
            sets=(put("quote", "quote", "started"),),
            dwell_hours=(0.05, 0.5),
            violation="quote started before the cover was viewed",
        ),
        EventSpec(
            "quote.submitted", (QU,),
            requires=(need("quote", "quote", "started"),),
            sets=(put("quote", "quote", "submitted"),),
            dwell_hours=(0.1, 4.0),
            violation="quote submitted before it started",
        ),
        EventSpec(
            "underwriting.started", (UW,),
            requires=(need("quote", "quote", "submitted"), need("quote", "underwriting", None)),
            sets=(put("quote", "underwriting", "pending"),),
            dwell_hours=(0.05, 1.0),
            violation="underwriting started before the quote was submitted",
        ),
        EventSpec(
            "underwriting.referred", (UW,),
            requires=(need("quote", "underwriting", "pending"),),
            sets=(put("quote", "underwriting", "referred"),),
            weight=0.25, outcome="underwriting_outcome", dwell_hours=(4.0, 72.0),
            violation="underwriting referred outside a pending underwriting",
        ),
        EventSpec(
            "underwriting.accepted", (UW,),
            requires=(need("quote", "underwriting", "pending", "referred"),),
            sets=(put("quote", "underwriting", "accepted"),),
            weight=0.8, outcome="underwriting_outcome", dwell_hours=(0.2, 48.0),
            violation="underwriting accepted outside a pending underwriting",
        ),
        EventSpec(
            "underwriting.declined", (UW,),
            requires=(need("quote", "underwriting", "pending", "referred"),),
            sets=(put("quote", "underwriting", "declined"),),
            weight=0.12, outcome="underwriting_outcome", ends_journey=True, dwell_hours=(0.2, 48.0),
            violation="underwriting declined outside a pending underwriting",
        ),
        EventSpec(
            "policy.bound", (PA,),
            requires=(need("quote", "underwriting", "accepted"), need("policy", "policy", None)),
            sets=(put("policy", "policy", "bound"), put("party", "relationship", "policyholder")),
            dwell_hours=(0.05, 6.0),
            violation="policy issued before underwriting acceptance",
        ),
        EventSpec(
            "policy.issued", (PA,),
            requires=(need("policy", "policy", "bound"),),
            sets=(put("policy", "policy", "in_force"),),
            dwell_hours=(0.2, 24.0),
            violation="policy issued before underwriting acceptance and binding",
        ),
        EventSpec(
            "premium.paid", (BI,),
            requires=(need("policy", "policy", *IN_FORCE),),
            sets=(put("policy", "billing", "paid"),),
            repeat=6, dwell_hours=(1.0, 72.0),
            violation="premium paid before policy issue",
        ),
        EventSpec(
            "claim.notified", (CL,),
            requires=(need("policy", "policy", *IN_FORCE), need("claim", "claim", None)),
            sets=(put("claim", "claim", "open"),),
            weight=0.5, dwell_hours=(DAY, 40 * DAY),
            violation="claim notified before policy issue",
        ),
        EventSpec(
            "claim.assessed", (CL,),
            requires=(need("claim", "claim", "open"),),
            sets=(put("claim", "claim", "assessed"),),
            dwell_hours=(DAY, 14 * DAY),
            violation="claim assessed before notification",
        ),
        EventSpec(
            "claim.settled", (CL,),
            requires=(need("claim", "claim", "assessed"),),
            sets=(put("claim", "claim", "settled"),),
            weight=0.75, outcome="claim_decision", dwell_hours=(DAY, 21 * DAY),
            violation="claim decided before assessment",
        ),
        EventSpec(
            "claim.denied", (CL,),
            requires=(need("claim", "claim", "assessed"),),
            sets=(put("claim", "claim", "denied"),),
            weight=0.25, outcome="claim_decision", dwell_hours=(4.0, 10 * DAY),
            violation="claim decided before assessment",
        ),
        EventSpec(
            "policy.renewed", (PA, SV),
            requires=(need("policy", "policy", "in_force"),),
            sets=(put("policy", "policy", "renewed"),),
            weight=0.4, outcome="policy_term", dwell_hours=(300 * DAY, 400 * DAY),
            violation="policy changed before issue",
        ),
        EventSpec(
            "policy.cancelled", (PA,),
            requires=(need("policy", "policy", *IN_FORCE),),
            sets=(put("policy", "policy", "cancelled"),),
            weight=0.03, outcome="policy_term", ends_journey=True, dwell_hours=(DAY, 60 * DAY),
            violation="policy changed before issue",
        ),
        EventSpec(
            "complaint.received", (SV, CO),
            requires=(need("party", "relationship", None, "policyholder"), need("complaint", "complaint", None)),
            sets=(put("complaint", "complaint", "open"),),
            weight=0.12, dwell_hours=(DAY, 30 * DAY),
            violation="complaint received twice or from a prospect mid-quote",
        ),
        EventSpec(
            "complaint.resolved", (CO,),
            requires=(need("complaint", "complaint", "open"),),
            sets=(put("complaint", "complaint", "resolved"),),
            weight=0.8, dwell_hours=(2 * DAY, 56 * DAY),
            violation="complaint resolved before it was received",
        ),
    ),
    milestones={
        QU: ("quote.submitted",),
        UW: ("underwriting.accepted", "underwriting.declined"),
        PA: ("policy.issued",),
        BI: ("premium.paid",),
        CL: ("claim.settled", "claim.denied"),
        SV: ("policy.renewed", "complaint.received"),
        CO: ("complaint.received",),
    },
)

OBJECTS = {
    "party": ("P", "party", "individual"),
    "offering": ("F", "product_offering", "motor"),
    "quote": ("U", "quote", "new_business"),
    "policy": ("Y", "policy", "motor"),
    "claim": ("H", "claim", "loss"),
    "complaint": ("M", "complaint", "service"),
}

ROLES = {
    "product.viewed": (("party", "prospect"), ("offering", "offering")),
    "quote.started": (("party", "applicant"), ("quote", "quote")),
    "quote.submitted": (("party", "applicant"), ("quote", "quote")),
    "underwriting.started": (("party", "applicant"), ("quote", "quote")),
    "underwriting.referred": (("party", "applicant"), ("quote", "quote")),
    "underwriting.accepted": (("party", "applicant"), ("quote", "quote")),
    "underwriting.declined": (("party", "applicant"), ("quote", "quote")),
    "policy.bound": (("party", "policyholder"), ("policy", "policy"), ("quote", "quote")),
    "policy.issued": (("party", "policyholder"), ("policy", "policy")),
    "premium.paid": (("party", "policyholder"), ("policy", "policy")),
    "claim.notified": (("party", "claimant"), ("claim", "claim"), ("policy", "policy")),
    "claim.assessed": (("claim", "claim"), ("policy", "policy")),
    "claim.settled": (("claim", "claim"), ("policy", "policy")),
    "claim.denied": (("claim", "claim"), ("policy", "policy")),
    "policy.renewed": (("party", "policyholder"), ("policy", "policy")),
    "policy.cancelled": (("party", "policyholder"), ("policy", "policy")),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
    "complaint.resolved": (("party", "complainant"), ("complaint", "case")),
}

EN = {
    "product.viewed": "The prospect viewed a retail cover.",
    "quote.started": "A quote was started.",
    "quote.submitted": "The quote was submitted.",
    "underwriting.started": "Underwriting started.",
    "underwriting.referred": "Underwriting was referred.",
    "underwriting.accepted": "Underwriting accepted the risk.",
    "underwriting.declined": "Underwriting declined the risk.",
    "policy.bound": "The policy was bound.",
    "policy.issued": "The policy was issued.",
    "premium.paid": "The premium was paid.",
    "claim.notified": "A claim was notified.",
    "claim.assessed": "The claim was assessed.",
    "claim.settled": "The claim was settled.",
    "claim.denied": "The claim was denied.",
    "policy.renewed": "The policy was renewed.",
    "policy.cancelled": "The policy was cancelled.",
    "complaint.received": "A complaint was received.",
    "complaint.resolved": "The complaint was resolved.",
}
TR = {
    "product.viewed": "Aday bir teminat inceledi.",
    "quote.started": "Teklif başladı.",
    "quote.submitted": "Teklif iletildi.",
    "underwriting.started": "Risk değerlendirmesi başladı.",
    "underwriting.referred": "Risk değerlendirmesi incelemeye alındı.",
    "underwriting.accepted": "Risk kabul edildi.",
    "underwriting.declined": "Risk reddedildi.",
    "policy.bound": "Poliçe bağlandı.",
    "policy.issued": "Poliçe düzenlendi.",
    "premium.paid": "Prim ödendi.",
    "claim.notified": "Hasar ihbarı yapıldı.",
    "claim.assessed": "Hasar incelendi.",
    "claim.settled": "Hasar ödendi.",
    "claim.denied": "Hasar reddedildi.",
    "policy.renewed": "Poliçe yenilendi.",
    "policy.cancelled": "Poliçe iptal edildi.",
    "complaint.received": "Şikayet kaydı açıldı.",
    "complaint.resolved": "Şikayet çözüldü.",
}

TRAJECTORY_TYPES = (
    "claim_denied",
    "claim_settled",
    "underwriting_declined",
    "underwriting_referral",
    "policy_cancelled",
    "policy_renewed",
    "complaint_case",
    "policy_in_force",
)


def classify(types: list[str]) -> str:
    present = set(types)
    if "claim.denied" in present:
        return "claim_denied"
    if "claim.settled" in present:
        return "claim_settled"
    if "underwriting.declined" in present:
        return "underwriting_declined"
    if "underwriting.referred" in present:
        return "underwriting_referral"
    if "policy.cancelled" in present:
        return "policy_cancelled"
    if "policy.renewed" in present:
        return "policy_renewed"
    if "complaint.received" in present:
        return "complaint_case"
    return "policy_in_force"


def success(types: list[str]) -> bool:
    if not types:
        return False
    return not ({"underwriting.declined", "claim.denied", "policy.cancelled"} & set(types))


def user_line(kind: str, lang: str) -> str:
    claim = kind in {"claim_settled", "claim_denied"}
    if lang == "tr":
        if claim:
            return "Hasar bildirimi yapmak istiyorum."
        if kind == "complaint_case":
            return "Bir şikayet iletmek istiyorum."
        return "Sigorta teklifi istiyorum."
    if claim:
        return "I need to claim on my policy."
    if kind == "complaint_case":
        return "I want to raise a complaint."
    return "I want a quote for cover."


def subtype(kind: str, default: str, steering: Any) -> str:
    if kind in {"offering", "policy"} and steering.products:
        return steering.products[0]
    return default


PACK = PackSpec(
    sector="insurance",
    generator_id=GENERATOR_ID,
    lifecycle=LIFECYCLE,
    default_domain=QU,
    objects=OBJECTS,
    roles=ROLES,
    phrases={"en": EN, "tr": TR},
    prompts={
        "en": "Simulate a synthetic retail-insurance journey. Do not invent real people, policy numbers, or claim references.",
        "tr": "Sentetik bir perakende sigorta yolculuğu üret. Gerçek kişi, poliçe veya hasar numarası uydurma.",
    },
    relationships=(
        ("party", "REQUESTED", "quote"),
        ("quote", "RESULTED_IN", "policy"),
        ("party", "HOLDS", "policy"),
        ("claim", "AGAINST", "policy"),
        ("party", "NOTIFIED", "claim"),
        ("party", "RAISED", "complaint"),
    ),
    system_events=frozenset(
        {
            "underwriting.referred",
            "underwriting.accepted",
            "underwriting.declined",
            "policy.bound",
            "policy.issued",
            "claim.assessed",
            "claim.settled",
            "claim.denied",
            "policy.renewed",
            "policy.cancelled",
            "complaint.resolved",
        }
    ),
    fixed_channels={},
    default_channels={
        "product.viewed": "web",
        "quote.started": "web",
        "quote.submitted": "web",
        "underwriting.started": "web",
        "premium.paid": "web",
        "claim.notified": "mobile",
        "complaint.received": "call_centre",
    },
    amounts={"premium.paid": (40.0, 900.0), "claim.settled": (80.0, 8000.0)},
    trajectory_types=TRAJECTORY_TYPES,
    classify=classify,
    success=success,
    user_line=user_line,
    subtype=subtype,
    correctness_drops=("claim.denied",),
)
