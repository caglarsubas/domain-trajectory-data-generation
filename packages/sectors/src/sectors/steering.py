"""Pull safe steering terms out of warm-start text.

Terms match on word boundaries, so "industry" is not the Turkish lira and
"discard" is not a card. Currency codes match only in capitals. The most
mentioned currency and channel win. An event mention preceded by a negation in
the same sentence ("no kyc.failed", "never application declined") does not name
the event. Names, addresses, account numbers, and keys are scrubbed first and
never reach a trajectory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SECRET = re.compile(r"\b(?:sk|xai|AIza)[A-Za-z0-9_\-]{6,}\b")
_LONG_NUMBER = re.compile(r"\d{6,}")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_NEGATION = re.compile(r"\b(?:no|not|never|without|none|nor|cannot|can't|don't|doesn't|didn't|isn't|wasn't|aren't|weren't)\b")
NEGATION_WINDOW = 4

CURRENCY_CODES = ("GBP", "USD", "EUR", "TRY")
CURRENCY_WORDS = (
    ("sterling", "GBP"),
    ("pounds", "GBP"),
    ("dollar", "USD"),
    ("dollars", "USD"),
    ("euro", "EUR"),
    ("euros", "EUR"),
    ("lira", "TRY"),
    ("£", "GBP"),
    ("$", "USD"),
    ("€", "EUR"),
    ("₺", "TRY"),
)


@dataclass(frozen=True)
class Vocabulary:
    channels: tuple[tuple[str, str], ...]
    products: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CorpusSteering:
    currency: str | None
    channel: str | None
    products: tuple[str, ...]
    terms: tuple[str, ...]
    events: tuple[str, ...] = ()
    negated: tuple[str, ...] = ()
    mentions: dict[str, int] = field(default_factory=dict)

    def report(self) -> dict:
        return {
            "currency": self.currency,
            "channel": self.channel,
            "products": list(self.products),
            "named_events": list(self.events),
            "negated_events": list(self.negated),
            "mentions": dict(self.mentions),
        }


EMPTY = CorpusSteering(None, None, (), ())


def scrub_text(text: str) -> str:
    cleaned = text.replace("\x00", " ")
    cleaned = _EMAIL.sub(" ", cleaned)
    cleaned = _SECRET.sub(" ", cleaned)
    cleaned = _LONG_NUMBER.sub(" ", cleaned)
    return cleaned


def _word(token: str) -> re.Pattern[str]:
    if not token[0].isalnum():
        return re.compile(re.escape(token))
    return re.compile(rf"(?<![\w.]){re.escape(token)}(?![\w])", re.IGNORECASE)


def steer(text: str, vocabulary: Vocabulary, namespace: tuple[str, ...]) -> CorpusSteering:
    clean = scrub_text(text)
    mentions: dict[str, int] = {}

    currencies: dict[str, int] = {}
    for code in CURRENCY_CODES:
        count = len(re.findall(rf"(?<![\w]){code}(?![\w])", clean))
        if count:
            currencies[code] = currencies.get(code, 0) + count
    for token, code in CURRENCY_WORDS:
        count = len(_word(token).findall(clean))
        if count:
            currencies[code] = currencies.get(code, 0) + count
    currency = _most(currencies, [code for code in CURRENCY_CODES])

    channels: dict[str, int] = {}
    for token, name in vocabulary.channels:
        count = len(_word(token).findall(clean))
        if count:
            channels[name] = channels.get(name, 0) + count
            mentions[token] = count
    channel = _most(channels, [name for _, name in vocabulary.channels])

    products: list[str] = []
    for token, name in vocabulary.products:
        count = len(_word(token).findall(clean))
        if count:
            mentions[token] = count
            if name not in products:
                products.append(name)

    events, negated = _events(clean, namespace)
    for code, count in currencies.items():
        mentions[code] = count
    terms = tuple(dict.fromkeys(item for item in (currency, channel, *products) if item))
    return CorpusSteering(
        currency=currency,
        channel=channel,
        products=tuple(products),
        terms=terms,
        events=events,
        negated=negated,
        mentions=mentions,
    )


def _most(counts: dict[str, int], order: list[str]) -> str | None:
    if not counts:
        return None
    return max(counts, key=lambda name: (counts[name], -order.index(name) if name in order else 0))


def _events(text: str, namespace: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    named: list[str] = []
    negated: list[str] = []
    for sentence in _SENTENCE.split(text):
        lowered = sentence.lower()
        for name in namespace:
            spaced = name.replace(".", " ").replace("_", " ")
            for form in (name, spaced):
                for match in re.finditer(rf"(?<![\w.]){re.escape(form)}(?![\w])", lowered):
                    before = lowered[: match.start()].split()[-NEGATION_WINDOW:]
                    if _NEGATION.search(" ".join(before)):
                        if name not in negated:
                            negated.append(name)
                    elif name not in named:
                        named.append(name)
    ordered = tuple(name for name in namespace if name in named)
    return ordered, tuple(name for name in namespace if name in negated and name not in named)
