"""Facts from warm-start documents, each with its evidence and how strongly the text supports it.

A fact is explicit when the text states it plainly: a currency code in capitals, a dotted event name, a
channel or product named in a sentence that does something with it. It is strongly implied when the text
only points at it: a currency symbol or word, an event named in plain words, a product or channel
mentioned in passing. Explicit facts steer the generator; implied facts wait for a person to accept them.
Matching reuses the steering rules: whole words, currency codes only in capitals, negation within a
sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sectors.steering import (
    CURRENCY_CODES,
    CURRENCY_WORDS,
    NEGATION_WINDOW,
    CorpusSteering,
    Vocabulary,
    _NEGATION,
    _SENTENCE,
    _word,
)

EXPLICIT, IMPLIED = "explicit", "implied"
EVIDENCE = 3
SENTENCE_CHARS = 280
ACTION = re.compile(
    r"\b(?:open|opens|opened|opening|apply|applies|applied|offer|offers|offered|provide|provides|provided|hold|holds|"
    r"issue|issues|issued|approve|approves|approved|fund|funds|funded|pay|pays|paid|transfer|transfers|sign up|signs up|"
    r"onboard|onboarded|log in|logs in|visit|visits|call|calls|use|uses|used|through|via|using|buy|buys|bought|claim|claims)\b",
    re.IGNORECASE,
)


@dataclass
class Fact:
    key: str
    kind: str
    value: str
    statement: str
    support: str
    confidence: float
    mentions: int = 0
    explicit_mentions: int = 0
    evidence: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "value": self.value,
            "statement": self.statement,
            "support": self.support,
            "confidence": self.confidence,
            "mentions": self.mentions,
            "evidence": self.evidence,
        }


STATEMENTS = {
    "currency": "Amounts are in {value}.",
    "channel": "Customers use the {value} channel.",
    "product": "The study covers {value} products.",
    "event": "Journeys include {value}.",
    "negated_event": "Journeys should not include {value}.",
}


def _note(found: dict[str, Fact], kind: str, value: str, explicit: bool, document: str, sentence: str) -> None:
    key = f"{kind}:{value}"
    fact = found.get(key)
    if fact is None:
        fact = found[key] = Fact(key, kind, value, STATEMENTS[kind].format(value=value.replace("_", " ")), IMPLIED, 0.0)
    fact.mentions += 1
    fact.explicit_mentions += int(explicit)
    quote = {"document": document, "sentence": sentence.strip()[:SENTENCE_CHARS], "explicit": explicit}
    if quote not in fact.evidence:
        fact.evidence.append(quote)


def extract_facts(documents: list[tuple[str, str]], vocabulary: Vocabulary, namespace: tuple[str, ...]) -> list[Fact]:
    """Facts from (document name, scrubbed text) pairs, in a stable order."""
    found: dict[str, Fact] = {}
    for name, text in documents:
        for sentence in _SENTENCE.split(text):
            if not sentence.strip():
                continue
            acts = bool(ACTION.search(sentence))
            for code in CURRENCY_CODES:
                if re.search(rf"(?<![\w]){code}(?![\w])", sentence):
                    _note(found, "currency", code, True, name, sentence)
            for token, code in CURRENCY_WORDS:
                if _word(token).search(sentence):
                    _note(found, "currency", code, False, name, sentence)
            for token, channel in vocabulary.channels:
                if _word(token).search(sentence):
                    _note(found, "channel", channel, acts, name, sentence)
            for token, product in vocabulary.products:
                if _word(token).search(sentence):
                    _note(found, "product", product, acts, name, sentence)
            lowered = sentence.lower()
            for event in namespace:
                for form, explicit in ((event, True), (event.replace(".", " ").replace("_", " "), False)):
                    for match in re.finditer(rf"(?<![\w.]){re.escape(form)}(?![\w])", lowered):
                        before = lowered[: match.start()].split()[-NEGATION_WINDOW:]
                        kind = "negated_event" if _NEGATION.search(" ".join(before)) else "event"
                        _note(found, kind, event, explicit, name, sentence)
    facts = []
    for fact in found.values():
        if fact.explicit_mentions:
            fact.support = EXPLICIT
            fact.confidence = round(min(0.99, 0.7 + 0.1 * fact.explicit_mentions), 2)
        else:
            fact.confidence = round(min(0.8, 0.4 + 0.1 * fact.mentions), 2)
        # Explicit quotes first, then the rest, a few of each fact.
        fact.evidence = sorted(fact.evidence, key=lambda item: not item["explicit"])[:EVIDENCE]
        facts.append(fact)
    named = {fact.value for fact in facts if fact.kind == "event"}
    # An event the text also names without a negation is named, as in steering.
    facts = [fact for fact in facts if not (fact.kind == "negated_event" and fact.value in named)]
    order = {"currency": 0, "channel": 1, "product": 2, "event": 3, "negated_event": 4}
    return sorted(facts, key=lambda fact: (order[fact.kind], -fact.mentions, fact.value))


def active(facts: list[Fact], reviews: dict[str, str]) -> list[Fact]:
    """The facts that steer: explicit ones nobody rejected, and implied ones somebody accepted."""
    return [
        fact
        for fact in facts
        if (fact.support == EXPLICIT and reviews.get(fact.key) != "rejected") or (fact.support == IMPLIED and reviews.get(fact.key) == "accepted")
    ]


def steering_from_facts(facts: list[Fact], reviews: dict[str, str], namespace: tuple[str, ...]) -> CorpusSteering:
    chosen = active(facts, reviews)

    def top(kind: str) -> str | None:
        options = [fact for fact in chosen if fact.kind == kind]
        return max(options, key=lambda fact: (fact.mentions, fact.value)).value if options else None

    currency, channel = top("currency"), top("channel")
    products = tuple(fact.value for fact in chosen if fact.kind == "product")
    named = {fact.value for fact in chosen if fact.kind == "event"}
    negated = {fact.value for fact in chosen if fact.kind == "negated_event"}
    return CorpusSteering(
        currency=currency,
        channel=channel,
        products=products,
        terms=tuple(dict.fromkeys(item for item in (currency, channel, *products) if item)),
        events=tuple(name for name in namespace if name in named),
        negated=tuple(name for name in namespace if name in negated and name not in named),
        mentions={fact.value: fact.mentions for fact in chosen if fact.kind in {"currency", "channel", "product"}},
    )
