"""A study's facts: what its documents state, what they only imply, and what they leave to defaults."""

from __future__ import annotations

from sectors.facts import EXPLICIT, IMPLIED, Fact, active, extract_facts, steering_from_facts
from sectors.jurisdictions import get_jurisdiction

from app.corpus_text import ordered, read_document

# The generator's currency when neither a jurisdiction nor a document names one.
LANGUAGE_CURRENCY = {"en": "GBP", "tr": "TRY"}


def study_facts(items: list, sector) -> list[Fact]:
    documents = [(doc.name, doc.body) for doc in (read_document(item) for item in ordered(items)) if doc.readable]
    return extract_facts(documents, sector.vocabulary, tuple(sector.event_namespace))


def steering_for(items: list, sector, reviews: dict | None):
    return steering_from_facts(study_facts(items, sector), reviews or {}, tuple(sector.event_namespace))


def unsupported(chosen: list[Fact], sector, sub_domains: list[str], jurisdiction: str, language: str) -> list[dict]:
    """What the run takes from its defaults because no steering fact covers it, and a jurisdiction overriding a document."""
    profile = get_jurisdiction(jurisdiction)
    kinds = {fact.kind for fact in chosen}
    notes = []
    documents_currency = next((fact.value for fact in sorted(chosen, key=lambda fact: -fact.mentions) if fact.kind == "currency"), None)
    if profile.currency and documents_currency and documents_currency != profile.currency:
        notes.append({"kind": "currency", "statement": f"The documents name {documents_currency}; the {profile.label} profile sets {profile.currency}, which is used."})
    elif "currency" not in kinds:
        source, code = (f"the {profile.label} profile", profile.currency) if profile.currency else (f"the language ({language})", LANGUAGE_CURRENCY.get(language.split("-")[0], "GBP"))
        notes.append({"kind": "currency", "statement": f"No document names a currency; amounts use {code} from {source}."})
    if "channel" not in kinds:
        notes.append({"kind": "channel", "statement": "No document names a channel; each event uses the pack's usual channel."})
    if "product" not in kinds:
        notes.append({"kind": "product", "statement": "No document names a product; objects use the pack's default products."})
    named = {fact.value for fact in chosen if fact.kind == "event"}
    for domain in sub_domains:
        events = {item.event_type for item in sector.lifecycle.events if domain in item.sub_domains}
        if events and not events & named:
            notes.append({"kind": "sub_domain", "statement": f"No steering fact names an event of {domain.replace('_', ' ')}; its journeys follow the pack alone."})
    return notes


def facts_report(items: list, sector, reviews: dict | None, *, sub_domains: list[str], jurisdiction: str, language: str) -> dict:
    reviews = reviews or {}
    facts = study_facts(items, sector)
    chosen = active(facts, reviews)
    keys = {fact.key for fact in chosen}
    rows = [{**fact.as_dict(), "review": reviews.get(fact.key), "steers": fact.key in keys} for fact in facts]
    return {
        "explicit": [row for row in rows if row["support"] == EXPLICIT],
        "implied": [row for row in rows if row["support"] == IMPLIED],
        "unsupported": unsupported(chosen, sector, sub_domains, jurisdiction, language),
        "counts": {
            "explicit": sum(1 for row in rows if row["support"] == EXPLICIT),
            "implied": sum(1 for row in rows if row["support"] == IMPLIED),
            "awaiting_review": sum(1 for row in rows if row["support"] == IMPLIED and not row["review"]),
            "accepted": sum(1 for row in rows if row["review"] == "accepted"),
            "rejected": sum(1 for row in rows if row["review"] == "rejected"),
            "steering": len(chosen),
        },
        "jurisdiction": get_jurisdiction(jurisdiction).describe(),
    }
