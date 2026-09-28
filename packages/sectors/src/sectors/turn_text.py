"""Provider-written turn text (decision 11): the skeleton a writer gets, and the checks every turn must pass.

The generator narrates each journey from the pack's phrase tables. On request, a provider model writes the assistant's
turns instead, from a skeleton: the customer's opening and follow-ups, and for each turn the events it covers in order,
each with its template sentence, its amount, and the time since the same event last happened. The model answers one
sentence per event, labelled with the event, and code checks every turn:

- every event of the turn, in order, and nothing else;
- the amount of each event that has one, and no other number but the ones the skeleton gives;
- no identifier, email address, or link the skeleton does not give;
- the run's language.

A turn that fails keeps its template, and so does one that copies its template word for word: it was not written. This module never calls a provider: the caller passes a writer, which takes a
sequence's skeleton and returns the model's turns, or None when it made no call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_SENTENCE_CHARS = 400
NUMBER = re.compile(r"\d[\d.,]*\d|\d")
IDENTIFIER = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{4,}\b")
CONTACT = re.compile(r"\S+@\S+\.\w+|https?://|www\.", re.IGNORECASE)
WORDS = re.compile(r"[a-zçğıöşü]+")
STOPWORDS = {
    "en": {"the", "and", "was", "were", "your", "has", "have", "had", "is", "to", "of", "a", "an", "it", "we", "you", "for", "been", "on", "with", "after", "this", "that", "our"},
    "tr": {"ve", "bir", "bu", "için", "ile", "olarak", "da", "de", "sizin", "oldu", "edildi", "yapıldı", "sonra", "önceki", "bizim", "hesabınız", "başvurunuz", "işleminiz", "olan", "tarafından"},
}
TURKISH_LETTERS = set("çğıöşüÇĞİÖŞÜ")
REASONS = ("events", "raw_event_name", "amount_missing", "invented_number", "invented_identifier", "wrong_language", "too_long", "empty", "copied_template")


@dataclass
class TurnPlan:
    """One assistant turn: its events in order and the template it replaces."""

    events: list[dict]
    template: str
    segment: Any = field(repr=False, default=None)


@dataclass
class SequencePlan:
    sequence_id: str
    trajectory_id: str | None
    language: str
    opening: str
    follow_ups: list[str]
    turns: list[TurnPlan]

    def skeleton(self) -> dict:
        """What the writer sends to the model: the conversation's frame and each turn's events, nothing else."""
        return {
            "language": self.language,
            "opening": self.opening,
            "follow_ups": self.follow_ups,
            "turns": [
                [{key: event[key] for key in ("event", "template", "amount", "since") if event.get(key)} for event in turn.events]
                for turn in self.turns
            ],
        }


def plan(sequence, member, phrases: dict[str, str], lang: str) -> SequencePlan:
    """A sequence's skeleton, with each assistant turn's events in the order the template narrates them."""
    from sectors.journeys import _narrate

    segments = [segment for context in sequence.contexts for segment in context.segments]
    assistant = [segment for segment in segments if segment.role == "assistant"]
    users = [segment.text for segment in segments if segment.role == "user"]
    sentences = [_narrate(phrases[name], detail) for name, detail in zip(member.types, member.details)]
    turns = []
    for segment, indices in zip(assistant, _groups(len(sentences), len(assistant))):
        events = []
        for index in indices:
            detail = member.details[index]
            amount = member.amounts[index]
            events.append({
                "event": member.types[index],
                "template": sentences[index],
                "amount": detail.split(", ")[0] if amount is not None and detail else None,
                "value": amount,
                "since": detail.split(", ")[-1] if detail and (amount is None or ", " in detail) else None,
                "allowed": _numbers(sentences[index], lang),
            })
        turns.append(TurnPlan(events=events, template=segment.text, segment=segment))
    return SequencePlan(
        sequence_id=sequence.sequence_id,
        trajectory_id=member.trajectory_id,
        language=lang,
        opening=users[0] if users else "",
        follow_ups=users[1:],
        turns=turns,
    )


def _groups(count: int, turns: int) -> list[list[int]]:
    """The event indices of each turn, split as the template splits its sentences."""
    parts = max(1, min(turns, count)) if count else 0
    if not parts:
        return []
    size, extra = divmod(count, parts)
    grouped, start = [], 0
    for index in range(parts):
        end = start + size + (1 if index < extra else 0)
        grouped.append(list(range(start, end)))
        start = end
    return grouped


def _values(token: str, lang: str | None = None) -> set[float]:
    """A number's readings: English (1,234.50) and Turkish (1.234,50), or only the given language's."""
    readings = {"en": token.replace(",", ""), "tr": token.replace(".", "").replace(",", ".")}
    found = set()
    for code, text in readings.items():
        if lang is not None and code != lang:
            continue
        try:
            found.add(round(float(text), 2))
        except ValueError:
            continue
    return found


def _numbers(text: str, lang: str) -> set[float]:
    """The numbers a template states, read as its language writes them."""
    values: set[float] = set()
    for token in NUMBER.findall(text):
        values |= _values(token, lang)
    return values


def language_ok(text: str, lang: str) -> bool:
    words = WORDS.findall(text.lower())
    counts = {code: sum(word in stop for word in words) for code, stop in STOPWORDS.items()}
    turkish = any(char in TURKISH_LETTERS for char in text)
    if lang == "tr":
        return (turkish or counts["tr"] > 0) and counts["tr"] >= counts["en"]
    if turkish or counts["tr"] > counts["en"]:
        return False
    # A short turn may hold no common word; a longer one in English holds some.
    return counts["en"] > 0 or len(words) < 6


def check_turn(turn: TurnPlan, sentences: Any, lang: str) -> tuple[str | None, str]:
    """(None, text) when the written turn passes, else (the first reason it fails, "")."""
    if not isinstance(sentences, list) or not sentences:
        return "empty", ""
    labels, texts = [], []
    for item in sentences:
        if not isinstance(item, dict):
            return "empty", ""
        labels.append(str(item.get("event", "")).strip())
        texts.append(re.sub(r"\s+", " ", str(item.get("text", ""))).strip())
    if labels != [event["event"] for event in turn.events]:
        return "events", ""
    for event, text in zip(turn.events, texts):
        if not text:
            return "empty", ""
        if len(text) > MAX_SENTENCE_CHARS:
            return "too_long", ""
        if event["event"] in text or re.search(r"\b\w+_\w+\b", text):
            return "raw_event_name", ""
        if CONTACT.search(text) or any(not NUMBER.fullmatch(token) for token in IDENTIFIER.findall(text) if token not in event["template"]):
            return "invented_identifier", ""
        allowed = set(event["allowed"])
        if event["value"] is not None:
            allowed.add(round(float(event["value"]), 2))
        # A number passes when either of its readings is one the skeleton gives.
        readings = [_values(token) for token in NUMBER.findall(text)]
        if event["value"] is not None and not any(round(float(event["value"]), 2) in values for values in readings):
            return "amount_missing", ""
        if any(not values & allowed for values in readings):
            return "invented_number", ""
    text = " ".join(texts)
    if not language_ok(text, lang):
        return "wrong_language", ""
    if _plain(text) == _plain(turn.template):
        return "copied_template", ""
    return None, text


def _plain(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


Writer = Callable[[SequencePlan], "dict | None"]


def rewrite(samples: list, groups: list, phrases: dict[str, str], lang: str, writer: Writer) -> dict:
    """Offer each sequence to the writer, keep every turn that passes the checks, and report what happened.

    The writer returns {"turns": [[{"event", "text"}, ...], ...], "written_by": label}, or None when it made no call.
    """
    report = {"sequences": 0, "asked": 0, "turns": 0, "written": 0, "kept_template": 0, "reasons": {}, "written_by": None}
    for sample, members in zip(samples, groups):
        for sequence, member in zip(sample.sequences, members):
            report["sequences"] += 1
            skeleton = plan(sequence, member, phrases, lang)
            answer = writer(skeleton)
            if answer is None:
                continue
            report["asked"] += 1
            report["written_by"] = report["written_by"] or answer.get("written_by")
            written = answer.get("turns")
            for index, turn in enumerate(skeleton.turns):
                report["turns"] += 1
                candidate = written[index] if isinstance(written, list) and index < len(written) else None
                reason, text = check_turn(turn, candidate, lang)
                if reason is None:
                    turn.segment.text = text
                    turn.segment.written_by = answer.get("written_by")
                    report["written"] += 1
                else:
                    report["kept_template"] += 1
                    report["reasons"][reason] = report["reasons"].get(reason, 0) + 1
    return report
