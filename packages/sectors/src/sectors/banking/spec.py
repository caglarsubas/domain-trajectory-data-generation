"""Retail banking as orthogonal state machines.

Dimensions follow the object-centric model in docs/banking: relationship on the
party, application, KYC, account and funding, card, credit on the loan, and the
complaint case. Weights are hand-set priors until data sources calibrate them.
"""

from __future__ import annotations

from typing import Any

from sectors.episodes import BIAN, SYSTEM, TASK
from sectors.journeys import Amount, PackSpec
from sectors.lifecycle import EventSpec, LifecycleSpec, need, put

GENERATOR_ID = "banking-semi-markov-v2"
PACK_VERSION = "banking-pack-5"

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
            outcome="application_completion", dwell_hours=(0.1, 2.0),
            violation="application submitted before it started",
        ),
        # An account or loan application the customer never finishes belongs to that product's lifecycle.
        EventSpec(
            "application.abandoned", (OD, DP, CC),
            requires=(need("application", "application", "started"),),
            sets=(put("application", "application", "abandoned"),),
            weight=0.15, outcome="application_completion", ends_journey=True, dwell_hours=(2.0, 7 * DAY),
            violation="application abandoned before it started or after it was submitted",
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
            "kyc.failed", (OD, RC, DP, CC),
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
            "application.declined", (OD, DP, CC),
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
                need("application", "application", "approved"),
                need("account", "account", None),
            ),
            sets=(put("account", "account", "active"), put("party", "relationship", "customer")),
            dwell_hours=(0.05, 2.0),
            violation="account opened before an approved application with verified KYC",
        ),
        # The first deposit follows opening; later ones, such as pay, arrive weekly to monthly.
        EventSpec(
            "account.funded", (DP, SV),
            requires=(need("account", "account", "active"),),
            sets=(put("account", "funding", "funded"),),
            weight=0.9, repeat=3, dwell_hours=(4.0, 96.0), cycle_hours=(7 * DAY, 35 * DAY),
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
            repeat=5, outcome="card_authorisation", dwell_hours=(0.2, 48.0),
            violation="card purchase without an active card and account",
        ),
        EventSpec(
            "card.purchase_declined", (CP,),
            requires=(need("card", "card", "active"), need("account", "account", "active")),
            weight=0.12, repeat=2, outcome="card_authorisation", dwell_hours=(0.2, 48.0),
            violation="card purchase declined without an active card and account",
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
            repeat=6, outcome="loan_performance", dwell_hours=(25 * DAY, 35 * DAY), cycle_hours=(28 * DAY, 35 * DAY),
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
            weight=0.8, outcome="complaint_outcome", dwell_hours=(2 * DAY, 56 * DAY),
            violation="complaint resolved before it was received",
        ),
        # The final response does not uphold the complaint.
        EventSpec(
            "complaint.rejected", (CO,),
            requires=(need("complaint", "complaint", "open"),),
            sets=(put("complaint", "complaint", "rejected"),),
            weight=0.4, outcome="complaint_outcome", dwell_hours=(2 * DAY, 56 * DAY),
            violation="complaint rejected before it was received",
        ),
        # A limit change, such as an arranged overdraft, is a servicing request the bank may refuse.
        EventSpec(
            "account.limit_change_requested", (SV,),
            requires=(need("account", "account", "active"), need("account", "limit", None)),
            sets=(put("account", "limit", "requested"),),
            weight=0.25, dwell_hours=(DAY, 60 * DAY),
            violation="limit change requested twice or on an account that is not active",
        ),
        EventSpec(
            "account.limit_changed", (SV,),
            requires=(need("account", "account", "active"), need("account", "limit", "requested")),
            sets=(put("account", "limit", "changed"),),
            weight=0.65, outcome="limit_decision", dwell_hours=(0.05, 72.0),
            violation="limit changed without a request or on an account that is not active",
        ),
        EventSpec(
            "account.limit_change_declined", (SV,),
            requires=(need("account", "account", "active"), need("account", "limit", "requested")),
            sets=(put("account", "limit", "declined"),),
            weight=0.35, outcome="limit_decision", dwell_hours=(0.05, 72.0),
            violation="limit change declined without a request or on an account that is not active",
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
        SV: ("account.funded", "account.limit_changed", "account.limit_change_declined", "complaint.received", "account.closed"),
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
    "application.abandoned": (("party", "applicant"), ("application", "application")),
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
    "card.purchase_declined": (("card", "card"), ("account", "account")),
    "loan.disbursed": (("party", "borrower"), ("loan", "loan"), ("application", "application")),
    "loan.repayment_received": (("party", "borrower"), ("loan", "loan")),
    "loan.delinquent": (("loan", "loan"),),
    "loan.cured": (("loan", "loan"),),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
    "complaint.resolved": (("party", "complainant"), ("complaint", "case")),
    "complaint.rejected": (("party", "complainant"), ("complaint", "case")),
    "account.limit_change_requested": (("party", "holder"), ("account", "account")),
    "account.limit_changed": (("party", "holder"), ("account", "account")),
    "account.limit_change_declined": (("party", "holder"), ("account", "account")),
    "account.closed": (("party", "holder"), ("account", "account")),
}

EN = {
    "product.viewed": "The prospect viewed a retail product.",
    "application.started": "The application started.",
    "application.submitted": "The application was submitted.",
    "application.abandoned": "The applicant left the application unfinished.",
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
    "card.purchase_declined": "A card purchase was declined.",
    "loan.disbursed": "The loan was disbursed.",
    "loan.repayment_received": "A loan repayment was received.",
    "loan.delinquent": "The loan became delinquent.",
    "loan.cured": "The loan returned to good standing.",
    "complaint.received": "A complaint was received.",
    "complaint.resolved": "The complaint was resolved.",
    "complaint.rejected": "The complaint was not upheld.",
    "account.limit_change_requested": "The customer asked to change the account limit.",
    "account.limit_changed": "The account limit was changed.",
    "account.limit_change_declined": "The limit change was declined.",
    "account.closed": "The account was closed.",
}
TR = {
    "product.viewed": "Aday perakende ürünü inceledi.",
    "application.started": "Başvuru başladı.",
    "application.submitted": "Başvuru iletildi.",
    "application.abandoned": "Başvuru yarım bırakıldı.",
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
    "card.purchase_declined": "Kartla alışveriş reddedildi.",
    "loan.disbursed": "Kredi tutarı aktarıldı.",
    "loan.repayment_received": "Kredi taksiti ödendi.",
    "loan.delinquent": "Kredi gecikmeye düştü.",
    "loan.cured": "Kredi yeniden düzenli ödemeye döndü.",
    "complaint.received": "Şikayet kaydı açıldı.",
    "complaint.resolved": "Şikayet çözüldü.",
    "complaint.rejected": "Şikayet haklı bulunmadı.",
    "account.limit_change_requested": "Müşteri hesap limitinin değiştirilmesini istedi.",
    "account.limit_changed": "Hesap limiti değiştirildi.",
    "account.limit_change_declined": "Limit değişikliği talebi reddedildi.",
    "account.closed": "Hesap kapatıldı.",
}

# Other phrasings of each event, stating the same fact as EN and TR.
EN_VARIANTS = {
    "product.viewed": (
        "A retail product was viewed by the prospect.",
        "The prospect looked at a retail product.",
        "The prospect browsed a retail product.",
        "The prospect checked a retail product.",
        "The prospect had a look at a retail product.",
        "A retail product was looked at by the prospect.",
        "The prospect took a look at a retail product.",
    ),
    "application.started": (
        "The application got under way.",
        "The application process began.",
        "The application was initiated.",
        "The application was begun.",
        "Work on the application began.",
        "The application was opened.",
        "An application was started.",
    ),
    "application.submitted": (
        "The application was sent in.",
        "The application was filed.",
        "The application was handed in.",
        "The application was lodged.",
        "The application went in.",
        "The application was put in.",
        "Submission of the application was completed.",
    ),
    "application.abandoned": (
        "The application was left unfinished by the applicant.",
        "The applicant did not finish the application.",
        "The applicant abandoned the application before finishing it.",
    ),
    "application.approved": (
        "The application received approval.",
        "Approval was granted for the application.",
        "The application was signed off.",
    ),
    "application.declined": (
        "The application was turned down.",
        "The application was rejected.",
        "The application was refused.",
    ),
    "kyc.started": (
        "Identity verification began.",
        "The identity checks got under way.",
        "Verification of identity was initiated.",
        "Identity checks began.",
        "Checks on identity started.",
        "Identity verification started.",
        "The identity checks were started.",
    ),
    "kyc.document_submitted": (
        "An identity document was sent in.",
        "An identification document was provided.",
        "A proof of identity was handed in.",
    ),
    "kyc.review_required": (
        "Identity checks were referred for manual review.",
        "Manual review was required for the identity checks.",
        "The identity checks were escalated to manual review.",
    ),
    "kyc.passed": (
        "Identity verification succeeded.",
        "The identity checks came back clear.",
        "Identity was confirmed by the checks.",
        "The identity checks were passed.",
        "Identity was verified.",
        "The identity checks were successful.",
        "Identity verification was passed.",
    ),
    "kyc.failed": (
        "Identity verification was unsuccessful.",
        "The identity checks did not pass.",
        "Identity could not be verified.",
    ),
    "account.opened": (
        "The account was set up.",
        "The account was established.",
        "The account went live.",
        "The account was created.",
        "Opening of the account was completed.",
        "The account opening went through.",
        "The account was put in place.",
    ),
    "account.funded": (
        "Money was deposited into the account.",
        "Funds were paid into the account.",
        "The account received a deposit.",
    ),
    "card.issued": (
        "The card was produced.",
        "Issuance of the card was completed.",
        "The card was provided.",
    ),
    "card.activated": (
        "The card became active.",
        "The card was enabled for use.",
        "Activation of the card was completed.",
    ),
    "card.purchase_authorised": (
        "A purchase made with the card was approved.",
        "Authorisation was granted for a card purchase.",
        "The card was authorised for a purchase.",
    ),
    "card.purchase_declined": (
        "A purchase made with the card was refused.",
        "Authorisation was refused for a card purchase.",
        "A card purchase was turned down.",
    ),
    "loan.disbursed": (
        "The loan was paid out.",
        "The loan amount was released.",
        "Disbursement of the loan was completed.",
    ),
    "loan.repayment_received": (
        "A repayment on the loan came in.",
        "A repayment was made on the loan.",
        "A loan instalment was paid.",
    ),
    "loan.delinquent": (
        "The loan fell into arrears.",
        "The loan slipped into delinquency.",
        "The loan became past due.",
    ),
    "loan.cured": (
        "The loan came out of arrears.",
        "The loan became current again.",
        "The loan was restored to good standing.",
    ),
    "complaint.received": (
        "A complaint came in.",
        "A complaint was registered.",
        "A complaint was lodged.",
    ),
    "complaint.resolved": (
        "A resolution was reached on the complaint.",
        "The complaint was closed as resolved.",
        "The complaint was sorted out.",
    ),
    "complaint.rejected": (
        "The complaint was rejected.",
        "The complaint was not found to be justified.",
        "The complaint was dismissed.",
    ),
    "account.limit_change_requested": (
        "The customer requested a change to the account limit.",
        "A request to change the account limit came from the customer.",
        "The customer applied for a change to the account limit.",
    ),
    "account.limit_changed": (
        "The account limit was adjusted.",
        "A change to the account limit took effect.",
        "The limit on the account was updated.",
    ),
    "account.limit_change_declined": (
        "The limit change request was turned down.",
        "The limit change was refused.",
        "The requested change to the limit was rejected.",
    ),
    "account.closed": (
        "The account was shut.",
        "Closure of the account was completed.",
        "The account was brought to a close.",
    ),
}
TR_VARIANTS = {
    "product.viewed": (
        "Aday bir perakende ürüne göz attı.",
        "Perakende ürün aday tarafından incelendi.",
        "Aday perakende ürünü gözden geçirdi.",
        "Aday bir perakende ürüne baktı.",
        "Perakende ürün aday tarafından görüntülendi.",
        "Aday perakende ürünü görüntüledi.",
        "Aday perakende ürünün ayrıntılarına baktı.",
    ),
    "application.started": (
        "Başvuru süreci başlatıldı.",
        "Başvuruya başlandı.",
        "Başvuru oluşturulmaya başlandı.",
        "Başvuru başlatıldı.",
        "Başvuru açıldı.",
        "Başvuru işlemi başladı.",
        "Başvuru işlemlerine başlandı.",
    ),
    "application.submitted": (
        "Başvuru gönderildi.",
        "Başvuru teslim edildi.",
        "Başvuru sunuldu.",
        "Başvurunun gönderimi tamamlandı.",
        "Başvuru iletimi yapıldı.",
        "Başvuru dosyası iletildi.",
        "Başvuru yapıldı.",
    ),
    "application.abandoned": (
        "Başvuru tamamlanmadan bırakıldı.",
        "Başvuru yarıda kaldı.",
        "Başvuru bitirilmeden terk edildi.",
    ),
    "application.approved": (
        "Başvuruya onay verildi.",
        "Başvuru olumlu sonuçlandı.",
        "Başvuru kabul edildi.",
    ),
    "application.declined": (
        "Başvuru kabul edilmedi.",
        "Başvuru olumsuz sonuçlandı.",
        "Başvuru geri çevrildi.",
    ),
    "kyc.started": (
        "Kimlik doğrulama süreci başlatıldı.",
        "Kimlik kontrolüne başlandı.",
        "Kimlik doğrulamasına geçildi.",
        "Kimlik kontrolü başlatıldı.",
        "Kimlik doğrulaması başladı.",
        "Kimlik kontrol süreci başladı.",
        "Kimlik kontrolü yapılmaya başlandı.",
    ),
    "kyc.document_submitted": (
        "Kimlik belgesi gönderildi.",
        "Kimlik belgesi teslim edildi.",
        "Bir kimlik belgesi sunuldu.",
    ),
    "kyc.review_required": (
        "Kimlik kontrolü incelemeye sevk edildi.",
        "Kimlik kontrolü için inceleme gerekti.",
        "Kimlik kontrolü inceleme aşamasına geçti.",
    ),
    "kyc.passed": (
        "Kimlik doğrulaması tamamlandı.",
        "Kimlik doğrulandı.",
        "Kimlik kontrolünden geçildi.",
        "Kimlik kontrolü olumlu sonuçlandı.",
        "Kimlik doğrulaması yapıldı.",
        "Kimlik bilgileri doğrulandı.",
        "Kimlik kontrolü sorunsuz tamamlandı.",
    ),
    "kyc.failed": (
        "Kimlik doğrulanamadı.",
        "Kimlik kontrolü başarısızlıkla sonuçlandı.",
        "Kimlik kontrolünden geçilemedi.",
    ),
    "account.opened": (
        "Hesap açılışı yapıldı.",
        "Hesap oluşturuldu.",
        "Hesabın açılış işlemi tamamlandı.",
        "Hesap açılışı tamamlandı.",
        "Hesap açılışı gerçekleşti.",
        "Hesap tanımlandı.",
        "Yeni hesap açıldı.",
    ),
    "account.funded": (
        "Hesaba para girişi yapıldı.",
        "Hesaba yatırma işlemi gerçekleşti.",
        "Hesaba para yüklendi.",
    ),
    "card.issued": (
        "Kart üretildi.",
        "Kartın basımı yapıldı.",
        "Kart hazırlandı.",
    ),
    "card.activated": (
        "Kart etkinleştirildi.",
        "Kart aktif hale getirildi.",
        "Kartın aktivasyonu tamamlandı.",
    ),
    "card.purchase_authorised": (
        "Kartla yapılan alışverişe onay verildi.",
        "Kart harcamasına provizyon verildi.",
        "Kartla yapılan harcama kabul edildi.",
    ),
    "card.purchase_declined": (
        "Kartla yapılan alışverişe onay verilmedi.",
        "Kart harcaması için provizyon alınamadı.",
        "Kartla yapılan harcama geri çevrildi.",
    ),
    "loan.disbursed": (
        "Kredi kullandırıldı.",
        "Kredi tutarının aktarımı yapıldı.",
        "Kredi tutarı transfer edildi.",
    ),
    "loan.repayment_received": (
        "Kredi taksiti tahsil edildi.",
        "Krediye taksit ödemesi yapıldı.",
        "Kredi için bir taksit ödemesi alındı.",
    ),
    "loan.delinquent": (
        "Kredi gecikmeye girdi.",
        "Kredide gecikme başladı.",
        "Kredi vadesi geçmiş duruma düştü.",
    ),
    "loan.cured": (
        "Kredi gecikmeden çıktı.",
        "Kredinin ödemeleri yeniden düzene girdi.",
        "Kredi yeniden düzenli ödenmeye başlandı.",
    ),
    "complaint.received": (
        "Bir şikayet alındı.",
        "Şikayet kayda alındı.",
        "Bir şikayet kaydı oluşturuldu.",
    ),
    "complaint.resolved": (
        "Şikayet çözüme kavuşturuldu.",
        "Şikayete çözüm bulundu.",
        "Şikayet çözülerek kapatıldı.",
    ),
    "complaint.rejected": (
        "Şikayet yerinde görülmedi.",
        "Şikayet kabul edilmedi.",
        "Şikayetin haklı olmadığına karar verildi.",
    ),
    "account.limit_change_requested": (
        "Müşteri hesap limiti için değişiklik talep etti.",
        "Hesap limitinin değiştirilmesi müşteri tarafından talep edildi.",
        "Müşteri hesap limitinde değişiklik yapılması için başvurdu.",
    ),
    "account.limit_changed": (
        "Hesap limiti güncellendi.",
        "Hesap limitinde değişiklik yapıldı.",
        "Hesabın limiti yeniden belirlendi.",
    ),
    "account.limit_change_declined": (
        "Limit değişikliği talebi kabul edilmedi.",
        "Limit değişikliği talebine onay verilmedi.",
        "Limitin değiştirilmesi isteği geri çevrildi.",
    ),
    "account.closed": (
        "Hesap kapandı.",
        "Hesabın kapanış işlemi yapıldı.",
        "Hesap sonlandırıldı.",
    ),
}

TRAJECTORY_TYPES = (
    "loan_delinquency",
    "application_declined",
    "application_abandoned",
    "kyc_review",
    "complaint_case",
    "limit_change",
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
    if "application.abandoned" in present:
        return "application_abandoned"
    if "kyc.review_required" in present:
        return "kyc_review"
    if "complaint.received" in present:
        return "complaint_case"
    if "account.limit_change_requested" in present:
        return "limit_change"
    if "account.closed" in present:
        return "account_closure"
    if "loan.disbursed" in present:
        return "loan_origination"
    if "card.purchase_authorised" in present:
        return "acquisition_to_first_purchase"
    if "account.funded" in present:
        return "deposit_funding"
    return "retail_journey"


# A journey holding one of these did not get what the customer came for.
FAILED_OUTCOMES = frozenset(
    {"application.abandoned", "application.declined", "kyc.failed", "complaint.rejected", "account.limit_change_declined"}
)


def success(types: list[str]) -> bool:
    if not types or FAILED_OUTCOMES & set(types):
        return False
    credit = [name for name in types if name in {"loan.delinquent", "loan.cured"}]
    if credit and credit[-1] != "loan.cured":
        return False
    # A later authorisation recovers from a declined purchase, as a cure recovers a delinquent loan.
    payments = [name for name in types if name in {"card.purchase_authorised", "card.purchase_declined"}]
    return not payments or payments[-1] == "card.purchase_authorised"


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
        "*": (
            "I want a current account.",
            "I would like to open an account with you.",
            "Can I open a current account online?",
            "I am thinking of opening an account with your bank.",
            "How do I open a current account?",
            "I would like to become a customer of your bank.",
            "I need a new bank account.",
            "I want to move my everyday banking to you.",
            "Could you help me open an account?",
            "I am interested in one of your accounts.",
            "I would like to look at your current accounts and maybe open one.",
            "I want to open an account for my salary.",
            "Can I sign up for a bank account with you?",
            "I would like some help getting an account set up.",
            "I want to start banking with you.",
        ),
        "acquisition_to_first_purchase": (
            "I want a current account and a debit card.",
            "I need an account with a card I can use straight away.",
            "I would like to open an account and get a debit card with it.",
            "Can I get a current account with a card for everyday spending?",
            "I want an account and a card so I can pay in shops.",
            "I need a current account and a debit card to pay for things.",
        ),
        "loan_origination": (
            "I want to apply for a loan.",
            "I would like to borrow for a car.",
            "Can I apply for a personal loan?",
            "I need to borrow some money.",
            "I would like to take out a personal loan.",
            "Could I get a loan to cover some home improvements?",
        ),
        "loan_delinquency": (
            "I want to apply for a loan.",
            "I would like a personal loan.",
            "I need a loan to cover some expenses.",
            "Can I borrow money from you?",
            "I would like to take out a loan.",
        ),
        "kyc_review": (
            "I want to open an account, but my documents are from abroad.",
            "I would like an account; my ID was issued recently.",
            "I want to open an account, but my address has changed since my ID was issued.",
            "Can I open an account with a foreign passport?",
            "I would like an account, but my name is spelled differently on my ID than on my other documents.",
            "I want an account, but I only have a temporary residence permit.",
        ),
        "@complaint.received": (
            "I want to raise a complaint.",
            "Something went wrong and I want to complain.",
            "I am not happy with your service and want to make a complaint.",
            "I need to file a complaint about my account.",
            "I would like to complain about how I was treated.",
            "Where can I lodge a complaint?",
        ),
    },
    "tr": {
        "*": (
            "Vadesiz bir hesap istiyorum.",
            "Sizde hesap açmak istiyorum.",
            "İnternetten vadesiz hesap açabilir miyim?",
            "Bankanızda hesap açmayı düşünüyorum.",
            "Vadesiz hesabı nasıl açabilirim?",
            "Bankanızın müşterisi olmak istiyorum.",
            "Yeni bir banka hesabına ihtiyacım var.",
            "Günlük bankacılık işlemlerimi size taşımak istiyorum.",
            "Hesap açmama yardımcı olabilir misiniz?",
            "Hesaplarınızdan biriyle ilgileniyorum.",
            "Vadesiz hesaplarınıza bakıp belki birini açmak istiyorum.",
            "Maaşım için bir hesap açmak istiyorum.",
            "Sizde banka hesabı açtırabilir miyim?",
            "Hesap açma konusunda yardım almak istiyorum.",
            "Bankanızla çalışmaya başlamak istiyorum.",
        ),
        "acquisition_to_first_purchase": (
            "Vadesiz hesap ve banka kartı istiyorum.",
            "Hemen kullanabileceğim kartlı bir hesap istiyorum.",
            "Hesap açıp yanında banka kartı almak istiyorum.",
            "Günlük harcamalar için kartlı bir vadesiz hesap alabilir miyim?",
            "Mağazalarda ödeme yapabilmek için hesap ve kart istiyorum.",
            "Alışverişlerimi ödemek için vadesiz hesap ve banka kartına ihtiyacım var.",
        ),
        "loan_origination": (
            "Kredi başvurusu yapmak istiyorum.",
            "Araç için kredi kullanmak istiyorum.",
            "İhtiyaç kredisine başvurabilir miyim?",
            "Kredi kullanmam gerekiyor.",
            "İhtiyaç kredisi çekmek istiyorum.",
            "Ev tadilatı için kredi alabilir miyim?",
        ),
        "loan_delinquency": (
            "Kredi başvurusu yapmak istiyorum.",
            "İhtiyaç kredisi istiyorum.",
            "Bazı masraflarımı karşılamak için krediye ihtiyacım var.",
            "Sizden kredi alabilir miyim?",
            "Kredi çekmek istiyorum.",
        ),
        "kyc_review": (
            "Hesap açmak istiyorum ama belgelerim yurt dışından.",
            "Hesap istiyorum; kimliğim yeni çıktı.",
            "Hesap açmak istiyorum ama kimliğim çıktıktan sonra adresim değişti.",
            "Yabancı pasaportla hesap açabilir miyim?",
            "Hesap istiyorum ama kimliğimdeki adımın yazılışı diğer belgelerimden farklı.",
            "Hesap açmak istiyorum ama yalnızca geçici oturma iznim var.",
        ),
        "@complaint.received": (
            "Bir şikayet iletmek istiyorum.",
            "Bir sorun yaşadım, şikayetçi olmak istiyorum.",
            "Hizmetinizden memnun değilim, şikayette bulunmak istiyorum.",
            "Hesabımla ilgili bir şikayet kaydı açtırmam gerekiyor.",
            "Bana karşı tutumunuzdan şikayetçiyim.",
            "Şikayetimi nereye iletebilirim?",
        ),
    },
}

FOLLOW_UPS = {
    "en": (
        "What happened next?",
        "Go on.",
        "And then?",
        "What did the bank do after that?",
        "Continue, please.",
        "What happened after that?",
        "Please keep going.",
        "What came next?",
        "Tell me more.",
        "Then what?",
        "Carry on, please.",
        "What was the next step?",
        "Keep going.",
        "And after that?",
        "What followed?",
        "Please continue.",
        "Okay, what next?",
        "I see. What happened then?",
        "Understood, go on.",
        "Could you tell me what happened next?",
    ),
    "tr": (
        "Sonra ne oldu?",
        "Devam edin.",
        "Ardından?",
        "Banka bundan sonra ne yaptı?",
        "Lütfen devam edin.",
        "Bundan sonra ne oldu?",
        "Lütfen sürdürün.",
        "Sırada ne vardı?",
        "Biraz daha anlatır mısınız?",
        "Peki sonra?",
        "Devam eder misiniz?",
        "Bir sonraki adım neydi?",
        "Anlatmaya devam edin.",
        "Ya ondan sonra?",
        "Arkasından ne geldi?",
        "Devam edebilirsiniz.",
        "Tamam, sonra ne oldu?",
        "Anladım. Peki ardından ne oldu?",
        "Anlaşıldı, devam edin.",
        "Sonra ne olduğunu anlatır mısınız?",
    ),
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
    variants={"en": EN_VARIANTS, "tr": TR_VARIANTS},
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
            "complaint.rejected",
            "account.limit_changed",
            "account.limit_change_declined",
        }
    ),
    fixed_channels={"card.purchase_authorised": "pos", "card.purchase_declined": "pos"},
    default_channels={
        "product.viewed": "web",
        "application.started": "web",
        "application.submitted": "web",
        "application.abandoned": "web",
        "kyc.started": "web",
        "kyc.document_submitted": "mobile",
        "account.funded": "mobile",
        "card.activated": "mobile",
        "account.limit_change_requested": "mobile",
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
        ("card.purchase_declined", "card"): "payment_instrument",
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
    operations=BIAN,
    agent={"en": {"system": SYSTEM["en"], "task": TASK["en"]}, "tr": {"system": SYSTEM["tr"], "task": TASK["tr"]}},
    goal={
        "en": "The journey ends without a failed outcome: no abandoned or declined application, failed KYC check, rejected complaint, or declined limit change; a delinquent loan is cured and the last card purchase is authorised.",
        "tr": "Yolculuk başarısız bir sonuç olmadan biter: terk edilen veya reddedilen başvuru, başarısız KYC kontrolü, reddedilen şikâyet veya reddedilen limit değişikliği olmaz; geciken kredi düzelir ve son kart harcaması onaylanır.",
    },
)
