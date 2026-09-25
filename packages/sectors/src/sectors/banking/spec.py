"""Retail banking as orthogonal state machines.

Dimensions follow the object-centric model in docs/banking: relationship on the
party, application, KYC, account and funding, card, credit on the loan, and the
complaint case. Weights are hand-set priors until data sources calibrate them.
"""

from __future__ import annotations

from typing import Any

from sectors.journeys import Amount, PackSpec
from sectors.lifecycle import EventSpec, LifecycleSpec, need, put

GENERATOR_ID = "banking-semi-markov-v2"
PACK_VERSION = "banking-pack-2"

OD = "onboarding_and_kyc"
RC = "risk_and_compliance"
DP = "deposits"
CP = "cards_and_payments"
CC = "consumer_credit"
SV = "servicing"
CO = "complaints"

DAY = 24.0

LIFECYCLE = LifecycleSpec(
    object_types={
        "party": "party",
        "offering": "product_offering",
        "application": "application",
        "kyc": "kyc_case",
        "account": "account",
        "card": "card",
        "loan": "loan",
        "complaint": "complaint",
    },
    events=(
        EventSpec(
            "product.viewed", (OD,),
            requires=(need("party", "relationship", None),),
            sets=(put("party", "relationship", "prospect"),),
            dwell_hours=(0.0, 0.0), opening=True,
        ),
        EventSpec(
            "application.started", (OD,),
            requires=(need("party", "relationship", "prospect"), need("application", "application", None)),
            sets=(put("application", "application", "started"),),
            dwell_hours=(0.05, 0.4),
            violation="application started before the product was viewed",
        ),
        EventSpec(
            "application.submitted", (OD,),
            requires=(need("application", "application", "started"),),
            sets=(put("application", "application", "submitted"),),
            dwell_hours=(0.1, 2.0),
            violation="application submitted before it started",
        ),
        EventSpec(
            "kyc.started", (OD,),
            requires=(need("application", "application", "submitted"), need("kyc", "kyc", None)),
            sets=(put("kyc", "kyc", "pending"),),
            dwell_hours=(0.02, 0.3),
            violation="KYC started before the application was submitted",
        ),
        EventSpec(
            "kyc.review_required", (RC,),
            requires=(need("kyc", "kyc", "pending", "documents_received"),),
            sets=(put("kyc", "kyc", "review_required"),),
            weight=0.25, outcome="kyc_outcome", dwell_hours=(0.1, 1.0),
            violation="KYC review outside an open KYC case",
        ),
        EventSpec(
            "kyc.document_submitted", (RC,),
            requires=(need("kyc", "kyc", "pending", "review_required", "documents_received"),),
            sets=(put("kyc", "kyc", "documents_received"),),
            weight=0.35, repeat=3, dwell_hours=(4.0, 72.0),
            violation="KYC document outside an open KYC case",
        ),
        EventSpec(
            "kyc.passed", (OD,),
            requires=(need("kyc", "kyc", "pending", "documents_received"),),
            sets=(put("kyc", "kyc", "verified"),),
            weight=0.75, outcome="kyc_outcome", dwell_hours=(0.05, 6.0),
            violation="KYC passed without an open case, or during review before documents arrived",
        ),
        EventSpec(
            "kyc.failed", (OD, RC),
            requires=(need("kyc", "kyc", "pending", "review_required", "documents_received"),),
            sets=(put("kyc", "kyc", "failed"),),
            weight=0.05, outcome="kyc_outcome", dwell_hours=(1.0, 48.0),
            violation="KYC failed outside an open KYC case",
        ),
        EventSpec(
            "application.approved", (OD, CC),
            requires=(need("application", "application", "submitted"), need("kyc", "kyc", "verified")),
            sets=(put("application", "application", "approved"),),
            weight=0.85, outcome="application_decision", dwell_hours=(0.2, 24.0),
            violation="application approved without a submitted application and verified KYC",
        ),
        EventSpec(
            "application.declined", (OD,),
            requires=(
                need("application", "application", "submitted"),
                need("kyc", "kyc", "verified", "failed"),
                need("account", "account", None),
                need("loan", "credit", None),
            ),
            sets=(put("application", "application", "declined"),),
            weight=0.12, outcome="application_decision", ends_journey=True, dwell_hours=(0.2, 24.0),
            violation="application declined before KYC concluded, or after it was decided or fulfilled",
        ),
        EventSpec(
            "account.opened", (DP,),
            requires=(
                need("kyc", "kyc", "verified"),
                need("application", "application", "submitted", "approved"),
                need("account", "account", None),
            ),
            sets=(put("account", "account", "active"), put("party", "relationship", "customer")),
            dwell_hours=(0.05, 2.0),
            violation="account opened before an application with verified KYC",
        ),
        EventSpec(
            "account.funded", (DP, SV),
            requires=(need("account", "account", "active"),),
            sets=(put("account", "funding", "funded"),),
            weight=0.9, repeat=3, dwell_hours=(4.0, 96.0),
            violation="account funded while not active",
        ),
        EventSpec(
            "card.issued", (CP,),
            requires=(need("account", "account", "active"), need("card", "card", None)),
            sets=(put("card", "card", "issued"),),
            weight=0.8, dwell_hours=(DAY, 4 * DAY),
            violation="card issued without an active account",
        ),
        EventSpec(
            "card.activated", (CP,),
            requires=(need("card", "card", "issued"),),
            sets=(put("card", "card", "active"),),
            weight=0.9, dwell_hours=(12.0, 7 * DAY),
            violation="card activated before issuance",
        ),
        EventSpec(
            "card.purchase_authorised", (CP,),
            requires=(need("card", "card", "active"), need("account", "account", "active")),
            repeat=5, dwell_hours=(0.2, 48.0),
            violation="card purchase without an active card and account",
        ),
        EventSpec(
            "loan.disbursed", (CC,),
            requires=(need("application", "application", "approved"), need("loan", "credit", None)),
            sets=(put("loan", "credit", "current"), put("party", "relationship", "customer")),
            dwell_hours=(DAY, 10 * DAY),
            violation="loan disbursed before application approval",
        ),
        EventSpec(
            "loan.repayment_received", (CC,),
            requires=(need("loan", "credit", "current"),),
            repeat=6, outcome="loan_performance", dwell_hours=(25 * DAY, 35 * DAY),
            violation="loan repayment while the loan is not current",
        ),
        EventSpec(
            "loan.delinquent", (CC,),
            requires=(need("loan", "credit", "current"),),
            sets=(put("loan", "credit", "delinquent"),),
            weight=0.06, outcome="loan_performance", dwell_hours=(30 * DAY, 120 * DAY),
            violation="loan delinquent while not current",
        ),
        EventSpec(
            "loan.cured", (CC,),
            requires=(need("loan", "credit", "delinquent"),),
            sets=(put("loan", "credit", "current"),),
            weight=0.6, dwell_hours=(5 * DAY, 45 * DAY),
            violation="loan cured while not delinquent",
        ),
        EventSpec(
            "complaint.received", (SV, CO),
            requires=(need("party", "relationship", None, "customer"), need("complaint", "complaint", None)),
            sets=(put("complaint", "complaint", "open"),),
            weight=0.12, dwell_hours=(DAY, 40 * DAY),
            violation="complaint received twice or from a prospect mid-application",
        ),
        EventSpec(
            "complaint.resolved", (CO,),
            requires=(need("complaint", "complaint", "open"),),
            sets=(put("complaint", "complaint", "resolved"),),
            weight=0.8, dwell_hours=(2 * DAY, 56 * DAY),
            violation="complaint resolved before it was received",
        ),
        EventSpec(
            "account.closed", (DP, SV),
            requires=(need("account", "account", "active"), need("card", "card", None, "issued")),
            sets=(put("account", "account", "closed"),),
            weight=0.03, ends_journey=True, dwell_hours=(30 * DAY, 400 * DAY),
            violation="account closed while not active or with an active card",
        ),
    ),
    milestones={
        OD: ("application.approved", "application.declined", "account.opened"),
        RC: ("kyc.review_required", "kyc.document_submitted", "kyc.failed"),
        DP: ("account.funded",),
        CP: ("card.activated", "card.purchase_authorised"),
        CC: ("loan.disbursed",),
        SV: ("account.funded", "complaint.received", "account.closed"),
        CO: ("complaint.received",),
    },
)

OBJECTS = {
    "party": ("P", "party", "individual"),
    "offering": ("F", "product_offering", "current"),
    "application": ("A", "application", "retail"),
    "kyc": ("K", "kyc_case", "onboarding"),
    "account": ("N", "account", "current"),
    "card": ("D", "card", "debit"),
    "loan": ("L", "loan", "personal"),
    "complaint": ("M", "complaint", "service"),
}

ROLES = {
    "product.viewed": (("party", "prospect"), ("offering", "offering")),
    "application.started": (("party", "applicant"), ("application", "application")),
    "application.submitted": (("party", "applicant"), ("application", "application")),
    "application.approved": (("party", "applicant"), ("application", "application")),
    "application.declined": (("party", "applicant"), ("application", "application")),
    "kyc.started": (("party", "subject"), ("kyc", "case")),
    "kyc.document_submitted": (("party", "subject"), ("kyc", "case")),
    "kyc.review_required": (("party", "subject"), ("kyc", "case")),
    "kyc.passed": (("party", "subject"), ("kyc", "case")),
    "kyc.failed": (("party", "subject"), ("kyc", "case")),
    "account.opened": (("party", "holder"), ("account", "account")),
    "account.funded": (("party", "holder"), ("account", "account")),
    "card.issued": (("account", "account"), ("card", "card")),
    "card.activated": (("card", "card"),),
    "card.purchase_authorised": (("card", "card"), ("account", "account")),
    "loan.disbursed": (("party", "borrower"), ("loan", "loan"), ("application", "application")),
    "loan.repayment_received": (("party", "borrower"), ("loan", "loan")),
    "loan.delinquent": (("loan", "loan"),),
    "loan.cured": (("loan", "loan"),),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
    "complaint.resolved": (("party", "complainant"), ("complaint", "case")),
    "account.closed": (("party", "holder"), ("account", "account")),
}

EN = {
    "product.viewed": "The prospect viewed a retail product.",
    "application.started": "The application started.",
    "application.submitted": "The application was submitted.",
    "application.approved": "The application was approved.",
    "application.declined": "The application was declined.",
    "kyc.started": "Identity checks started.",
    "kyc.document_submitted": "An identity document was submitted.",
    "kyc.review_required": "Identity checks went to manual review.",
    "kyc.passed": "Identity checks passed.",
    "kyc.failed": "Identity checks failed.",
    "account.opened": "The account was opened.",
    "account.funded": "The account was funded.",
    "card.issued": "The card was issued.",
    "card.activated": "The card was activated.",
    "card.purchase_authorised": "A card purchase was authorised.",
    "loan.disbursed": "The loan was disbursed.",
    "loan.repayment_received": "A loan repayment was received.",
    "loan.delinquent": "The loan became delinquent.",
    "loan.cured": "The loan returned to good standing.",
    "complaint.received": "A complaint was received.",
    "complaint.resolved": "The complaint was resolved.",
    "account.closed": "The account was closed.",
}
TR = {
    "product.viewed": "Aday perakende ürünü inceledi.",
    "application.started": "Başvuru başladı.",
    "application.submitted": "Başvuru iletildi.",
    "application.approved": "Başvuru onaylandı.",
    "application.declined": "Başvuru reddedildi.",
    "kyc.started": "Kimlik kontrolü başladı.",
    "kyc.document_submitted": "Kimlik belgesi iletildi.",
    "kyc.review_required": "Kimlik kontrolü incelemeye alındı.",
    "kyc.passed": "Kimlik kontrolü tamamlandı.",
    "kyc.failed": "Kimlik kontrolü başarısız oldu.",
    "account.opened": "Hesap açıldı.",
    "account.funded": "Hesaba para yatırıldı.",
    "card.issued": "Kart basıldı.",
    "card.activated": "Kart kullanıma açıldı.",
    "card.purchase_authorised": "Kartla alışveriş onaylandı.",
    "loan.disbursed": "Kredi tutarı aktarıldı.",
    "loan.repayment_received": "Kredi taksiti ödendi.",
    "loan.delinquent": "Kredi gecikmeye düştü.",
    "loan.cured": "Kredi yeniden düzenli ödemeye döndü.",
    "complaint.received": "Şikayet kaydı açıldı.",
    "complaint.resolved": "Şikayet çözüldü.",
    "account.closed": "Hesap kapatıldı.",
}

TRAJECTORY_TYPES = (
    "loan_delinquency",
    "application_declined",
    "kyc_review",
    "complaint_case",
    "account_closure",
    "loan_origination",
    "acquisition_to_first_purchase",
    "deposit_funding",
    "retail_journey",
)


def classify(types: list[str]) -> str:
    present = set(types)
    if "loan.delinquent" in present:
        return "loan_delinquency"
    if "application.declined" in present:
        return "application_declined"
    if "kyc.review_required" in present:
        return "kyc_review"
    if "complaint.received" in present:
        return "complaint_case"
    if "account.closed" in present:
        return "account_closure"
    if "loan.disbursed" in present:
        return "loan_origination"
    if "card.purchase_authorised" in present:
        return "acquisition_to_first_purchase"
    if "account.funded" in present:
        return "deposit_funding"
    return "retail_journey"


def success(types: list[str]) -> bool:
    if not types or "application.declined" in types or "kyc.failed" in types:
        return False
    credit = [name for name in types if name in {"loan.delinquent", "loan.cured"}]
    return not credit or credit[-1] == "loan.cured"


PROMPTS = {
    "en": (
        "Simulate a synthetic retail-banking journey. Do not invent real people or account numbers.",
        "Narrate a synthetic retail-banking customer journey step by step. Use no real names or account numbers.",
        "Walk through a simulated retail-banking case from first contact onward. Keep every identifier synthetic.",
    ),
    "tr": (
        "Sentetik bir perakende bankacılık yolculuğu üret. Gerçek kişi veya hesap numarası uydurma.",
        "Sentetik bir perakende bankacılık müşteri yolculuğunu adım adım anlat. Gerçek isim ya da hesap numarası kullanma.",
        "İlk temastan başlayarak simüle edilmiş bir bankacılık vakasını anlat. Tüm tanımlayıcılar sentetik olsun.",
    ),
}

OPENINGS = {
    "en": {
        "*": ("I want a current account.", "I would like to open an account with you.", "Can I open a current account online?"),
        "acquisition_to_first_purchase": ("I want a current account and a debit card.", "I need an account with a card I can use straight away."),
        "loan_origination": ("I want to apply for a loan.", "I would like to borrow for a car.", "Can I apply for a personal loan?"),
        "loan_delinquency": ("I want to apply for a loan.", "I would like a personal loan."),
        "kyc_review": ("I want to open an account, but my documents are from abroad.", "I would like an account; my ID was issued recently."),
        "@complaint.received": ("I want to raise a complaint.", "Something went wrong and I want to complain."),
    },
    "tr": {
        "*": ("Vadesiz bir hesap istiyorum.", "Sizde hesap açmak istiyorum.", "İnternetten vadesiz hesap açabilir miyim?"),
        "acquisition_to_first_purchase": ("Vadesiz hesap ve banka kartı istiyorum.", "Hemen kullanabileceğim kartlı bir hesap istiyorum."),
        "loan_origination": ("Kredi başvurusu yapmak istiyorum.", "Araç için kredi kullanmak istiyorum.", "İhtiyaç kredisine başvurabilir miyim?"),
        "loan_delinquency": ("Kredi başvurusu yapmak istiyorum.", "İhtiyaç kredisi istiyorum."),
        "kyc_review": ("Hesap açmak istiyorum ama belgelerim yurt dışından.", "Hesap istiyorum; kimliğim yeni çıktı."),
        "@complaint.received": ("Bir şikayet iletmek istiyorum.", "Bir sorun yaşadım, şikayetçi olmak istiyorum."),
    },
}

FOLLOW_UPS = {
    "en": ("What happened next?", "Go on.", "And then?", "What did the bank do after that?", "Continue, please."),
    "tr": ("Sonra ne oldu?", "Devam edin.", "Ardından?", "Banka bundan sonra ne yaptı?", "Lütfen devam edin."),
}


def intent(types: list[str]) -> str | None:
    if "loan.disbursed" in types:
        return "loan"
    if "card.issued" in types:
        return "card"
    if "account.opened" in types:
        return "account"
    return None


def subtype(kind: str, default: str, steering: Any) -> str:
    if kind in {"offering", "account"} and "savings" in steering.products:
        return "savings"
    if kind in {"offering", "loan"} and "mortgage" in steering.products:
        return "mortgage"
    if kind == "offering" and "loan" in steering.products:
        return "loan"
    return default


PACK = PackSpec(
    sector="banking",
    generator_id=GENERATOR_ID,
    pack_version=PACK_VERSION,
    lifecycle=LIFECYCLE,
    default_domain=OD,
    languages=("en", "tr"),
    objects=OBJECTS,
    roles=ROLES,
    phrases={"en": EN, "tr": TR},
    prompts=PROMPTS,
    openings=OPENINGS,
    follow_ups=FOLLOW_UPS,
    relationships=(
        ("party", "APPLIED_FOR", "application"),
        ("application", "RESULTED_IN", "account"),
        ("application", "RESULTED_IN", "loan"),
        ("card", "LINKED_TO", "account"),
        ("party", "HOLDS", "account"),
        ("party", "RAISED", "complaint"),
    ),
    system_events=frozenset(
        {
            "kyc.passed",
            "kyc.failed",
            "kyc.review_required",
            "application.approved",
            "application.declined",
            "account.opened",
            "card.issued",
            "loan.disbursed",
            "loan.repayment_received",
            "loan.delinquent",
            "loan.cured",
            "complaint.resolved",
        }
    ),
    fixed_channels={"card.purchase_authorised": "pos"},
    default_channels={
        "product.viewed": "web",
        "application.started": "web",
        "application.submitted": "web",
        "kyc.started": "web",
        "kyc.document_submitted": "mobile",
        "account.funded": "mobile",
        "card.activated": "mobile",
        "complaint.received": "call_centre",
        "account.closed": "branch",
    },
    amounts={
        "account.funded": Amount(80.0, 4000.0, "credit", "deposit"),
        "card.purchase_authorised": Amount(4.0, 180.0, "debit", "purchase"),
        "loan.disbursed": Amount(1500.0, 20000.0, "credit", "principal"),
        "loan.repayment_received": Amount(60.0, 900.0, "debit", "repayment"),
    },
    qualifiers={
        ("account.funded", "account"): "credited_account",
        ("card.issued", "account"): "linked_account",
        ("card.purchase_authorised", "card"): "payment_instrument",
        ("card.purchase_authorised", "account"): "debited_account",
        ("loan.disbursed", "application"): "originating_application",
        ("loan.disbursed", "loan"): "disbursed_loan",
        ("loan.repayment_received", "loan"): "repaid_loan",
        ("account.closed", "account"): "closed_account",
    },
    effective_lag_hours={
        # A card purchase posts to the account after authorisation; transfers settle the same or next day.
        "card.purchase_authorised": (12.0, 72.0),
        "account.funded": (0.1, 24.0),
        "loan.disbursed": (2.0, 30.0),
        "loan.repayment_received": (0.1, 24.0),
        "account.closed": (DAY, 7 * DAY),
    },
    trajectory_types=TRAJECTORY_TYPES,
    classify=classify,
    success=success,
    subtype=subtype,
    correctness_drops=("loan.delinquent",),
    intent=intent,
)
