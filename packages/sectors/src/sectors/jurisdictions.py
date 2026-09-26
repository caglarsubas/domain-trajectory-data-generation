"""Jurisdiction profiles: neutral retail, Turkey, and the United Kingdom.

A profile sets the currency, the local names of products, the KYC rules the judge checks and the sample
text states, and the language a study starts in. Neutral retail names no country: currency follows the
language, as before profiles existed.
"""

from __future__ import annotations

from dataclasses import dataclass


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

    def describe(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "currency": self.currency,
            "language": self.language,
            "products": dict(self.products),
            "kyc": list(self.kyc),
            "documents": list(self.documents),
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
