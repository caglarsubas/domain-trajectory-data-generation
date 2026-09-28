"""Retail insurance as orthogonal state machines.

Dimensions: relationship on the party, quote and underwriting on the quote,
policy and billing on the policy, the claim, and the complaint case. Weights
are hand-set priors until data sources calibrate them.
"""

from __future__ import annotations

from typing import Any

from sectors.journeys import Amount, PackSpec
from sectors.lifecycle import EventSpec, LifecycleSpec, need, put

GENERATOR_ID = "insurance-semi-markov-v2"
PACK_VERSION = "insurance-pack-4"

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
            weight=0.85, outcome="quote_completion", dwell_hours=(0.1, 4.0),
            violation="quote submitted before it started",
        ),
        EventSpec(
            "quote.abandoned", (QU,),
            requires=(need("quote", "quote", "started"),),
            sets=(put("quote", "quote", "abandoned"),),
            weight=0.15, outcome="quote_completion", ends_journey=True, dwell_hours=(2.0, 7 * DAY),
            violation="quote abandoned before it started or after it was submitted",
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
        # The first premium is taken at issue; later ones fall due monthly.
        EventSpec(
            "premium.paid", (BI,),
            requires=(need("policy", "policy", *IN_FORCE),),
            sets=(put("policy", "billing", "paid"),),
            weight=0.9, repeat=6, outcome="premium_outcome", dwell_hours=(1.0, 72.0), cycle_hours=(28 * DAY, 35 * DAY),
            violation="premium paid before policy issue",
        ),
        EventSpec(
            "premium.missed", (BI,),
            requires=(need("policy", "policy", *IN_FORCE), need("policy", "billing", None, "paid")),
            sets=(put("policy", "billing", "missed"),),
            weight=0.1, repeat=2, outcome="premium_outcome", dwell_hours=(20 * DAY, 40 * DAY),
            violation="premium missed before policy issue or while one is already missed",
        ),
        EventSpec(
            "policy.lapsed", (BI, PA),
            requires=(need("policy", "billing", "missed"), need("policy", "policy", *IN_FORCE)),
            sets=(put("policy", "policy", "lapsed"),),
            weight=0.5, ends_journey=True, dwell_hours=(14 * DAY, 30 * DAY),
            violation="policy lapsed without a missed premium",
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
            "policy.renewal_declined", (PA, SV),
            requires=(need("policy", "policy", "in_force"),),
            sets=(put("policy", "policy", "not_renewed"),),
            weight=0.06, outcome="policy_term", ends_journey=True, dwell_hours=(300 * DAY, 400 * DAY),
            violation="renewal declined before policy issue",
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
            weight=0.75, outcome="complaint_outcome", dwell_hours=(2 * DAY, 56 * DAY),
            violation="complaint resolved before it was received",
        ),
        EventSpec(
            "complaint.rejected", (CO,),
            requires=(need("complaint", "complaint", "open"),),
            sets=(put("complaint", "complaint", "rejected"),),
            weight=0.25, outcome="complaint_outcome", dwell_hours=(2 * DAY, 56 * DAY),
            violation="complaint rejected before it was received",
        ),
    ),
    milestones={
        QU: ("quote.submitted", "quote.abandoned"),
        UW: ("underwriting.accepted", "underwriting.declined"),
        PA: ("policy.issued",),
        BI: ("premium.paid", "premium.missed"),
        CL: ("claim.settled", "claim.denied"),
        SV: ("policy.renewed", "policy.renewal_declined", "complaint.received"),
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
    "quote.abandoned": (("party", "applicant"), ("quote", "quote")),
    "underwriting.started": (("party", "applicant"), ("quote", "quote")),
    "underwriting.referred": (("party", "applicant"), ("quote", "quote")),
    "underwriting.accepted": (("party", "applicant"), ("quote", "quote")),
    "underwriting.declined": (("party", "applicant"), ("quote", "quote")),
    "policy.bound": (("party", "policyholder"), ("policy", "policy"), ("quote", "quote")),
    "policy.issued": (("party", "policyholder"), ("policy", "policy")),
    "premium.paid": (("party", "policyholder"), ("policy", "policy")),
    "premium.missed": (("party", "policyholder"), ("policy", "policy")),
    "policy.lapsed": (("party", "policyholder"), ("policy", "policy")),
    "claim.notified": (("party", "claimant"), ("claim", "claim"), ("policy", "policy")),
    "claim.assessed": (("claim", "claim"), ("policy", "policy")),
    "claim.settled": (("claim", "claim"), ("policy", "policy")),
    "claim.denied": (("claim", "claim"), ("policy", "policy")),
    "policy.renewed": (("party", "policyholder"), ("policy", "policy")),
    "policy.renewal_declined": (("party", "policyholder"), ("policy", "policy")),
    "policy.cancelled": (("party", "policyholder"), ("policy", "policy")),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
    "complaint.resolved": (("party", "complainant"), ("complaint", "case")),
    "complaint.rejected": (("party", "complainant"), ("complaint", "case")),
}

EN = {
    "product.viewed": "The prospect viewed a retail cover.",
    "quote.started": "A quote was started.",
    "quote.submitted": "The quote was submitted.",
    "quote.abandoned": "The applicant left the quote unfinished.",
    "underwriting.started": "Underwriting started.",
    "underwriting.referred": "Underwriting was referred.",
    "underwriting.accepted": "Underwriting accepted the risk.",
    "underwriting.declined": "Underwriting declined the risk.",
    "policy.bound": "The policy was bound.",
    "policy.issued": "The policy was issued.",
    "premium.paid": "The premium was paid.",
    "premium.missed": "A premium payment was missed.",
    "policy.lapsed": "The policy lapsed for non-payment.",
    "claim.notified": "A claim was notified.",
    "claim.assessed": "The claim was assessed.",
    "claim.settled": "The claim was settled.",
    "claim.denied": "The claim was denied.",
    "policy.renewed": "The policy was renewed.",
    "policy.renewal_declined": "The insurer declined to renew the policy.",
    "policy.cancelled": "The policy was cancelled.",
    "complaint.received": "A complaint was received.",
    "complaint.resolved": "The complaint was resolved.",
    "complaint.rejected": "The complaint was rejected.",
}
TR = {
    "product.viewed": "Aday bir teminat inceledi.",
    "quote.started": "Teklif başladı.",
    "quote.submitted": "Teklif iletildi.",
    "quote.abandoned": "Başvuru sahibi teklifi yarım bıraktı.",
    "underwriting.started": "Risk değerlendirmesi başladı.",
    "underwriting.referred": "Risk değerlendirmesi incelemeye alındı.",
    "underwriting.accepted": "Risk kabul edildi.",
    "underwriting.declined": "Risk reddedildi.",
    "policy.bound": "Poliçe bağlandı.",
    "policy.issued": "Poliçe düzenlendi.",
    "premium.paid": "Prim ödendi.",
    "premium.missed": "Bir prim ödemesi kaçırıldı.",
    "policy.lapsed": "Poliçe ödeme yapılmadığı için sona erdi.",
    "claim.notified": "Hasar ihbarı yapıldı.",
    "claim.assessed": "Hasar incelendi.",
    "claim.settled": "Hasar ödendi.",
    "claim.denied": "Hasar reddedildi.",
    "policy.renewed": "Poliçe yenilendi.",
    "policy.renewal_declined": "Sigorta şirketi poliçeyi yenilemedi.",
    "policy.cancelled": "Poliçe iptal edildi.",
    "complaint.received": "Şikayet kaydı açıldı.",
    "complaint.resolved": "Şikayet çözüldü.",
    "complaint.rejected": "Şikayet reddedildi.",
}

# Other phrasings of each event, stating the same fact as EN and TR.
EN_VARIANTS = {
    "product.viewed": (
        "A retail cover was viewed by the prospect.",
        "The prospect looked at a retail cover.",
        "The prospect took a look at a retail cover.",
        "The prospect browsed a retail cover.",
        "The prospect checked a retail cover.",
        "The prospect had a look at a retail cover.",
        "A retail cover was looked at by the prospect.",
    ),
    "quote.started": (
        "A quote was initiated.",
        "A quote got under way.",
        "A quote was opened.",
        "A quote was begun.",
        "Work on a quote began.",
        "A quote was set in motion.",
        "A quote process started.",
    ),
    "quote.submitted": (
        "The quote was sent in.",
        "The quote was handed in.",
        "The quote request was lodged.",
        "The quote was lodged.",
        "The quote went in.",
        "Submission of the quote was completed.",
        "The quote was put in.",
    ),
    "quote.abandoned": (
        "The quote was left unfinished by the applicant.",
        "The applicant did not finish the quote.",
        "The applicant stopped partway through the quote.",
    ),
    "underwriting.started": (
        "Underwriting began.",
        "The underwriting process got under way.",
        "The underwriting review commenced.",
        "Underwriting got under way.",
        "Work on underwriting started.",
        "The underwriting process began.",
        "The underwriting stage began.",
    ),
    "underwriting.referred": (
        "The underwriting case was referred.",
        "Underwriting went to referral.",
        "A referral was raised in underwriting.",
    ),
    "underwriting.accepted": (
        "The risk was accepted by underwriting.",
        "Underwriting agreed to take on the risk.",
        "Underwriting approved the risk.",
    ),
    "underwriting.declined": (
        "The risk was declined by underwriting.",
        "Underwriting turned down the risk.",
        "Underwriting refused to accept the risk.",
    ),
    "policy.bound": (
        "The policy became bound.",
        "Binding of the policy took place.",
        "Cover on the policy was bound.",
        "Binding was completed on the policy.",
        "The binding of the policy went through.",
        "The policy reached binding.",
        "The policy moved to bound status.",
    ),
    "policy.issued": (
        "The policy got issued.",
        "Issuance of the policy took place.",
        "The issuing of the policy was completed.",
    ),
    "premium.paid": (
        "Payment of the premium was made.",
        "The premium payment went through.",
        "The premium was settled.",
    ),
    "premium.missed": (
        "A premium went unpaid.",
        "A premium payment was not made.",
        "A premium payment failed to come in.",
    ),
    "policy.lapsed": (
        "Non-payment caused the policy to lapse.",
        "The policy went into lapse through non-payment.",
        "The policy lapsed because payment was not made.",
    ),
    "claim.notified": (
        "A claim was reported.",
        "Notice of a claim was given.",
        "A claim was lodged.",
    ),
    "claim.assessed": (
        "An assessment of the claim was carried out.",
        "The claim was evaluated.",
        "The claim went through assessment.",
    ),
    "claim.settled": (
        "The claim was paid out.",
        "The claim reached settlement.",
        "A settlement was made on the claim.",
    ),
    "claim.denied": (
        "The claim was turned down.",
        "The claim was rejected.",
        "The claim ended in a denial.",
    ),
    "policy.renewed": (
        "Renewal of the policy went ahead.",
        "The policy got renewed.",
        "The policy underwent renewal.",
    ),
    "policy.renewal_declined": (
        "The insurer chose not to renew the policy.",
        "Renewal of the policy was declined by the insurer.",
        "The insurer refused renewal of the policy.",
    ),
    "policy.cancelled": (
        "Cancellation of the policy took place.",
        "The policy got cancelled.",
        "The policy went through cancellation.",
    ),
    "complaint.received": (
        "A complaint came in.",
        "A complaint was logged.",
        "Receipt of a complaint was recorded.",
    ),
    "complaint.resolved": (
        "The complaint reached a resolution.",
        "The complaint was sorted out.",
        "Resolution of the complaint was achieved.",
    ),
    "complaint.rejected": (
        "The complaint was not upheld.",
        "The complaint was turned down.",
        "The complaint was dismissed.",
    ),
}
TR_VARIANTS = {
    "product.viewed": (
        "Bir teminat aday tarafından incelendi.",
        "Aday bir teminata göz attı.",
        "Aday bir teminatı gözden geçirdi.",
        "Aday bir teminata baktı.",
        "Bir teminat aday tarafından görüntülendi.",
        "Aday bir teminatı görüntüledi.",
        "Aday bir teminatın ayrıntılarına baktı.",
    ),
    "quote.started": (
        "Bir teklif başlatıldı.",
        "Teklif süreci başladı.",
        "Teklif oluşturulmaya başlandı.",
        "Teklif başlatıldı.",
        "Teklif işlemi başladı.",
        "Teklife başlandı.",
        "Teklif hazırlanmaya başlandı.",
    ),
    "quote.submitted": (
        "Teklif gönderildi.",
        "Teklif talebi iletildi.",
        "Teklifin gönderimi yapıldı.",
        "Teklif teslim edildi.",
        "Teklifin iletimi yapıldı.",
        "Teklif sunuldu.",
        "Teklif gönderimi tamamlandı.",
    ),
    "quote.abandoned": (
        "Teklif başvuru sahibi tarafından yarım bırakıldı.",
        "Başvuru sahibi teklifi tamamlamadı.",
        "Başvuru sahibi teklifi bitirmeden bıraktı.",
    ),
    "underwriting.started": (
        "Risk değerlendirme süreci başladı.",
        "Riskin değerlendirilmesine başlandı.",
        "Risk değerlendirmesine geçildi.",
        "Risk değerlendirmesi başlatıldı.",
        "Risk değerlendirmesine başlandı.",
        "Risk değerlendirme aşaması başladı.",
        "Risk değerlendirmesi yapılmaya başlandı.",
    ),
    "underwriting.referred": (
        "Risk değerlendirmesi incelemeye sevk edildi.",
        "Risk değerlendirmesi inceleme aşamasına geçti.",
        "Risk değerlendirmesi incelenmek üzere yönlendirildi.",
    ),
    "underwriting.accepted": (
        "Risk onaylandı.",
        "Riskin kabulüne karar verildi.",
        "Risk değerlendirmede kabul gördü.",
    ),
    "underwriting.declined": (
        "Risk kabul edilmedi.",
        "Riskin reddine karar verildi.",
        "Risk değerlendirmede kabul görmedi.",
    ),
    "policy.bound": (
        "Poliçenin bağlanması gerçekleşti.",
        "Poliçe bağlama işlemi tamamlandı.",
        "Poliçe için teminat bağlandı.",
        "Poliçenin bağlanması tamamlandı.",
        "Poliçe için bağlama yapıldı.",
        "Poliçe bağlı duruma geçti.",
        "Poliçe bağlama işlemi gerçekleşti.",
    ),
    "policy.issued": (
        "Poliçe tanzim edildi.",
        "Poliçenin düzenlenmesi tamamlandı.",
        "Poliçe düzenleme işlemi gerçekleştirildi.",
    ),
    "premium.paid": (
        "Prim ödemesi yapıldı.",
        "Prim tahsil edildi.",
        "Primin ödemesi gerçekleşti.",
    ),
    "premium.missed": (
        "Bir prim ödenmedi.",
        "Bir prim ödemesi yapılmadı.",
        "Bir prim ödemesi gerçekleşmedi.",
    ),
    "policy.lapsed": (
        "Ödeme yapılmaması nedeniyle poliçe sona erdi.",
        "Ödeme yapılmadığından poliçe geçerliliğini yitirdi.",
        "Poliçe, ödeme gerçekleşmediği için son buldu.",
    ),
    "claim.notified": (
        "Hasar bildirildi.",
        "Bir hasar ihbar edildi.",
        "Hasar bildiriminde bulunuldu.",
    ),
    "claim.assessed": (
        "Hasar değerlendirildi.",
        "Hasar dosyası incelendi.",
        "Hasarın incelemesi yapıldı.",
    ),
    "claim.settled": (
        "Hasar tazminatı ödendi.",
        "Hasar için ödeme yapıldı.",
        "Hasar dosyası ödendi.",
    ),
    "claim.denied": (
        "Hasar talebi kabul edilmedi.",
        "Hasarın reddine karar verildi.",
        "Hasar dosyası ret ile sonuçlandı.",
    ),
    "policy.renewed": (
        "Poliçenin yenilemesi gerçekleşti.",
        "Poliçe yenileme işlemi tamamlandı.",
        "Poliçe için yenileme yapıldı.",
    ),
    "policy.renewal_declined": (
        "Sigorta şirketi poliçeyi yenilememeye karar verdi.",
        "Poliçe, sigorta şirketi tarafından yenilenmedi.",
        "Sigorta şirketi poliçenin yenilenmesini reddetti.",
    ),
    "policy.cancelled": (
        "Poliçenin iptali gerçekleşti.",
        "Poliçe iptal işlemi tamamlandı.",
        "Poliçe iptal oldu.",
    ),
    "complaint.received": (
        "Bir şikayet alındı.",
        "Şikayet kayda geçirildi.",
        "Bir şikayet kaydı oluşturuldu.",
    ),
    "complaint.resolved": (
        "Şikayet çözüme kavuşturuldu.",
        "Şikayetin çözümü sağlandı.",
        "Şikayet giderildi.",
    ),
    "complaint.rejected": (
        "Şikayet kabul edilmedi.",
        "Şikayet yerinde bulunmadı.",
        "Şikayetin reddine karar verildi.",
    ),
}

# Insurance capability domains, with the action terms the episode builder uses for every pack.
OPERATIONS = {
    "product.viewed": ("Product Catalog", "Retrieve"),
    "quote.started": ("Quote Management", "Initiate"),
    "quote.submitted": ("Quote Management", "Update"),
    "quote.abandoned": ("Quote Management", "Control"),
    "underwriting.started": ("Underwriting", "Initiate"),
    "underwriting.referred": ("Underwriting", "Request"),
    "underwriting.accepted": ("Underwriting", "Evaluate"),
    "underwriting.declined": ("Underwriting", "Evaluate"),
    "policy.bound": ("Policy Administration", "Initiate"),
    "policy.issued": ("Policy Administration", "Execute"),
    "premium.paid": ("Premium Billing", "Execute"),
    "premium.missed": ("Premium Billing", "Update"),
    "policy.lapsed": ("Policy Administration", "Control"),
    "claim.notified": ("Claims Management", "Initiate"),
    "claim.assessed": ("Claims Management", "Evaluate"),
    "claim.settled": ("Claims Management", "Execute"),
    "claim.denied": ("Claims Management", "Execute"),
    "policy.renewed": ("Policy Administration", "Update"),
    "policy.renewal_declined": ("Policy Administration", "Evaluate"),
    "policy.cancelled": ("Policy Administration", "Control"),
    "complaint.received": ("Customer Case Management", "Initiate"),
    "complaint.resolved": ("Customer Case Management", "Execute"),
    "complaint.rejected": ("Customer Case Management", "Execute"),
}

TRAJECTORY_TYPES = (
    "quote_abandoned",
    "policy_lapsed",
    "policy_not_renewed",
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
    if "quote.abandoned" in present:
        return "quote_abandoned"
    if "policy.lapsed" in present:
        return "policy_lapsed"
    if "policy.renewal_declined" in present:
        return "policy_not_renewed"
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
    if {"quote.abandoned", "underwriting.declined", "claim.denied", "policy.cancelled", "policy.lapsed", "policy.renewal_declined", "complaint.rejected"} & set(types):
        return False
    # A missed premium is recovered only when a later one is paid.
    premiums = [name for name in types if name in {"premium.paid", "premium.missed"}]
    return not premiums or premiums[-1] == "premium.paid"


PROMPTS = {
    "en": (
        "Simulate a synthetic retail-insurance journey. Do not invent real people, policy numbers, or claim references.",
        "Narrate a synthetic retail-insurance customer journey step by step. Keep every policy and claim reference synthetic.",
        "Walk through a simulated retail-insurance case from the first quote onward. Use no real names or numbers.",
    ),
    "tr": (
        "Sentetik bir perakende sigorta yolculuğu üret. Gerçek kişi, poliçe veya hasar numarası uydurma.",
        "Sentetik bir perakende sigorta müşteri yolculuğunu adım adım anlat. Poliçe ve hasar numaraları sentetik olsun.",
        "İlk tekliften başlayarak simüle edilmiş bir sigorta vakasını anlat. Gerçek isim ya da numara kullanma.",
    ),
}

OPENINGS = {
    "en": {
        "*": (
            "I want a quote for cover.",
            "Can you quote me for motor insurance?",
            "I would like to insure my car.",
            "I am looking for an insurance policy.",
            "Could I get a price for cover?",
            "I would like to take out a policy.",
            "How much would it cost to insure my car?",
            "I need insurance cover and would like a quote.",
            "Can you help me get insured?",
            "I am shopping around for car insurance.",
            "Could you give me a quote for a new policy?",
            "I would like to see what cover you offer.",
            "I want to arrange insurance with you.",
        ),
        "underwriting_referral": (
            "I want a quote; I have had a claim before.",
            "Can I get cover with a past conviction?",
            "I need a quote, but I have points on my licence.",
            "I would like cover, though my car has been modified.",
            "Can I still get insured after a previous accident?",
        ),
        "@complaint.received": (
            "I want to raise a complaint.",
            "I am unhappy with how my policy was handled.",
            "I would like to make a formal complaint.",
            "I need to complain about the service I received.",
            "I am not happy with how I was treated and want to complain.",
        ),
    },
    "tr": {
        "*": (
            "Sigorta teklifi istiyorum.",
            "Kasko için teklif alabilir miyim?",
            "Aracımı sigortalatmak istiyorum.",
            "Bir sigorta poliçesi arıyorum.",
            "Teminat için fiyat alabilir miyim?",
            "Bir poliçe yaptırmak istiyorum.",
            "Aracımı sigortalatmanın maliyeti ne olur?",
            "Sigorta teminatına ihtiyacım var, teklif almak istiyorum.",
            "Sigortalanmama yardımcı olabilir misiniz?",
            "Araç sigortası için farklı seçeneklere bakıyorum.",
            "Yeni bir poliçe için teklif verebilir misiniz?",
            "Hangi teminatları sunduğunuzu görmek istiyorum.",
            "Sizinle sigorta yaptırmak istiyorum.",
        ),
        "underwriting_referral": (
            "Teklif istiyorum; daha önce hasarım oldu.",
            "Geçmiş bir cezam varken teminat alabilir miyim?",
            "Teklif istiyorum ama ehliyetimde ceza puanı var.",
            "Aracımda modifikasyon yapıldı, yine de teminat almak istiyorum.",
            "Daha önce kaza yapmış olsam da sigortalanabilir miyim?",
        ),
        "@complaint.received": (
            "Bir şikayet iletmek istiyorum.",
            "Poliçemle ilgili süreçten memnun değilim.",
            "Resmi bir şikayette bulunmak istiyorum.",
            "Aldığım hizmetten şikayetçiyim.",
            "Bana yapılan muameleden memnun değilim ve şikayet etmek istiyorum.",
        ),
    },
}

FOLLOW_UPS = {
    "en": (
        "What happened next?",
        "Go on.",
        "And then?",
        "What did the insurer do after that?",
        "Continue, please.",
        "What happened after that?",
        "Please carry on.",
        "What came next?",
        "Keep going.",
        "And after that?",
        "What was the next step?",
        "Then what?",
        "Tell me more.",
        "Please continue.",
        "What followed?",
        "Go ahead.",
        "And what happened then?",
        "Anything after that?",
        "Could you continue?",
        "Where did it go from there?",
        "Okay, what next?",
    ),
    "tr": (
        "Sonra ne oldu?",
        "Devam edin.",
        "Ardından?",
        "Sigorta şirketi bundan sonra ne yaptı?",
        "Lütfen devam edin.",
        "Bundan sonra ne oldu?",
        "Lütfen sürdürün.",
        "Sırada ne vardı?",
        "Devam edebilirsiniz.",
        "Peki sonra?",
        "Bir sonraki adım neydi?",
        "Sonrasında?",
        "Biraz daha anlatır mısınız?",
        "Anlatmaya devam edin lütfen.",
        "Ardından ne geldi?",
        "Buyurun, devam edin.",
        "Peki o zaman ne oldu?",
        "Başka bir şey oldu mu?",
        "Devam eder misiniz?",
        "Oradan sonra nasıl ilerledi?",
        "Tamam, sonra ne oldu?",
    ),
}


def subtype(kind: str, default: str, steering: Any) -> str:
    if kind in {"offering", "policy"} and steering.products:
        return steering.products[0]
    return default


PACK = PackSpec(
    sector="insurance",
    generator_id=GENERATOR_ID,
    pack_version=PACK_VERSION,
    lifecycle=LIFECYCLE,
    default_domain=QU,
    languages=("en", "tr"),
    objects=OBJECTS,
    roles=ROLES,
    phrases={"en": EN, "tr": TR},
    variants={"en": EN_VARIANTS, "tr": TR_VARIANTS},
    prompts=PROMPTS,
    openings=OPENINGS,
    follow_ups=FOLLOW_UPS,
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
    amounts={
        "premium.paid": Amount(40.0, 900.0, "debit", "premium"),
        "claim.settled": Amount(80.0, 8000.0, "credit", "claim_payment"),
    },
    qualifiers={
        ("policy.bound", "quote"): "originating_quote",
        ("premium.paid", "policy"): "billed_policy",
        ("claim.notified", "policy"): "covering_policy",
        ("claim.settled", "claim"): "paid_claim",
        ("claim.denied", "claim"): "denied_claim",
    },
    effective_lag_hours={
        # Cover starts on the policy's start date; a settlement reaches the customer after approval.
        "policy.issued": (DAY, 14 * DAY),
        "policy.renewed": (DAY, 30 * DAY),
        "premium.paid": (0.1, 48.0),
        "claim.settled": (DAY, 5 * DAY),
        "policy.cancelled": (DAY, 30 * DAY),
    },
    trajectory_types=TRAJECTORY_TYPES,
    classify=classify,
    success=success,
    subtype=subtype,
    correctness_drops=("claim.denied",),
    operations=OPERATIONS,
    agent={
        "en": {
            "system": "You are an operations agent at a retail insurer. Use only the listed operations and only the case's own identifiers.",
            "task": "You operate the insurer's systems for customer {party}. {situation} Decide the next step and record it with one operation call, then say what you did.",
        },
        "tr": {
            "system": "Bir perakende sigorta şirketinde operasyon temsilcisisiniz. Yalnızca listelenen işlemleri ve yalnızca bu vakanın kimliklerini kullanın.",
            "task": "{party} numaralı müşteri için sigorta şirketinin sistemlerini yönetiyorsunuz. {situation} Sıradaki adıma karar verin, tek bir işlem çağrısıyla kaydedin ve ne yaptığınızı söyleyin.",
        },
    },
    goal={
        "en": "The journey ends without a failed outcome: no abandoned quote, declined risk, denied claim, cancelled, lapsed, or unrenewed policy, or rejected complaint; a missed premium is paid later.",
        "tr": "Yolculuk başarısız bir sonuç olmadan biter: yarım bırakılan teklif, reddedilen risk, reddedilen hasar, iptal edilen, sona eren veya yenilenmeyen poliçe ya da reddedilen şikâyet olmaz; kaçırılan prim sonradan ödenir.",
    },
)
