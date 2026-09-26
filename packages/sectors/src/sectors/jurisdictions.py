"""Jurisdiction profiles: neutral retail, Turkey, and the United Kingdom.

A profile sets the currency, the local names of products, the KYC rules the judge checks and the sample
text states, and the language a study starts in. Neutral retail names no country: currency follows the
language, as before profiles existed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Jurisdiction:
    id: str
    label: str
    currency: str | None
    language: str | None
    # Local product names by object subtype: current, savings, loan, personal, mortgage, debit, and insurance lines.
    products: dict[str, str]
    kyc: tuple[str, ...]
    documents: tuple[str, ...]
    # Rules for sectors where KYC is not the point, such as number porting for telecoms, by sector id.
    rules: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def rules_for(self, sector: str) -> tuple[str, tuple[str, ...]]:
        """The heading and rules a sector's samples and judge see: its own rules, else the KYC rules."""
        if sector in self.rules:
            return "Rules", self.rules[sector]
        return "KYC", self.kyc

    def describe(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "currency": self.currency,
            "language": self.language,
            "products": dict(self.products),
            "kyc": list(self.kyc),
            "documents": list(self.documents),
            "rules": {sector: list(rules) for sector, rules in self.rules.items()},
        }


PROFILES: dict[str, Jurisdiction] = {
    "neutral": Jurisdiction(
        id="neutral",
        label="Neutral retail",
        currency=None,
        language=None,
        products={
            "current": "current account",
            "savings": "savings account",
            "loan": "personal loan",
            "personal": "personal loan",
            "mortgage": "mortgage",
            "debit": "debit card",
            "motor": "motor insurance",
            "home": "home insurance",
            "travel": "travel insurance",
            "life": "life insurance",
            "mobile_postpaid": "pay-monthly mobile plan",
            "mobile_prepaid": "prepaid SIM",
            "sim_only": "SIM-only plan",
            "broadband": "home broadband",
            "fibre": "fibre broadband",
        },
        rules={
            "telecom": (
                "Identity is checked before a contract starts or a SIM is activated.",
                "A customer who switches provider can keep their number.",
            ),
        },
        kyc=(
            "Identity is verified before an account is opened or a policy is bound.",
            "Higher-risk customers get enhanced due diligence.",
        ),
        documents=("government-issued photo ID", "proof of address"),
    ),
    "uk": Jurisdiction(
        id="uk",
        label="United Kingdom",
        currency="GBP",
        language="en",
        products={
            "current": "current account",
            "savings": "easy-access savings account",
            "loan": "personal loan",
            "personal": "personal loan",
            "mortgage": "residential mortgage",
            "debit": "contactless debit card",
            "motor": "comprehensive car insurance",
            "home": "buildings and contents insurance",
            "travel": "travel insurance",
            "life": "term life insurance",
            "mobile_postpaid": "pay monthly contract",
            "mobile_prepaid": "pay as you go SIM",
            "sim_only": "SIM-only deal",
            "broadband": "broadband",
            "fibre": "full-fibre broadband",
        },
        rules={
            "telecom": (
                "A mobile customer can switch provider by text and keep their number, and the code to do so is free.",
                "A complaint not resolved within eight weeks can go to an Ofcom-approved dispute resolution scheme.",
            ),
        },
        kyc=(
            "Identity and address are verified under the Money Laundering Regulations 2017 before an account is opened.",
            "Politically exposed persons and higher-risk customers get enhanced due diligence.",
            "Complaints are resolved within eight weeks or referred to the Financial Ombudsman Service.",
        ),
        documents=("passport or UK photocard driving licence", "utility bill or bank statement dated within three months"),
    ),
    "tr": Jurisdiction(
        id="tr",
        label="Turkey",
        currency="TRY",
        language="tr",
        products={
            "current": "vadesiz hesap",
            "savings": "vadeli mevduat hesabı",
            "loan": "ihtiyaç kredisi",
            "personal": "ihtiyaç kredisi",
            "mortgage": "konut kredisi",
            "debit": "banka kartı",
            "motor": "kasko",
            "home": "konut sigortası",
            "travel": "seyahat sağlık sigortası",
            "life": "hayat sigortası",
            "mobile_postpaid": "faturalı hat",
            "mobile_prepaid": "faturasız hat",
            "sim_only": "cihazsız tarife",
            "broadband": "ev interneti",
            "fibre": "fiber internet",
        },
        rules={
            "telecom": (
                "Aboneler, BTK düzenlemelerine göre hat açılmadan önce kimlik doğrulamasından geçer.",
                "Müşteri, numara taşıma ile numarasını koruyarak operatör değiştirebilir.",
            ),
        },
        kyc=(
            "Kimlik, MASAK düzenlemelerine göre T.C. kimlik numarasıyla hesap açılmadan önce doğrulanır.",
            "Uzaktan müşteri edinimi BDDK kurallarına göre görüntülü görüşmeyle yapılır.",
            "Yüksek riskli müşteriler için sıkılaştırılmış tedbirler uygulanır.",
        ),
        documents=("çipli T.C. kimlik kartı", "yerleşim yeri belgesi"),
    ),
}

DEFAULT = "neutral"


def get_jurisdiction(profile: str | None) -> Jurisdiction:
    key = (profile or DEFAULT).strip().lower()
    if key not in PROFILES:
        raise ValueError(f"unknown jurisdiction: {profile}; choose one of {', '.join(PROFILES)}")
    return PROFILES[key]
