"""Choose the warm-start passages the judge reads, instead of the first 2,000 characters.

Documents are cut into passages on paragraph boundaries and ranked with BM25 against a query made of
the study's sub-domains and the names of their events. The best passages fill a character budget and
are returned in document order, each labelled with its source.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.corpus_text import ordered, read_document

TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
PASSAGE_CHARS = 800
STOPWORDS = set(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "ve bir bu da de ile için icin olarak olan gibi daha çok cok en ya".split()
)


@dataclass(frozen=True)
class Passage:
    source: str
    index: int
    text: str


def tokens(text: str) -> list[str]:
    return [word for word in (item.lower() for item in TOKEN.findall(text)) if word not in STOPWORDS and len(word) > 1]


def split_passages(source: str, text: str, size: int = PASSAGE_CHARS) -> list[Passage]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n|\n(?=[-*#] )", text) if block.strip()]
    passages: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > size:
            # A long paragraph is cut at sentence ends, after what came before it.
            if current:
                passages.append(current)
                current = ""
            while len(block) > size:
                cut = block.rfind(". ", 0, size)
                cut = cut + 1 if cut > size // 2 else size
                passages.append(block[:cut].strip())
                block = block[cut:].strip()
        if not block:
            continue
        if current and len(current) + len(block) + 1 > size:
            passages.append(current)
            current = block
        else:
            current = f"{current}\n{block}" if current else block
    if current:
        passages.append(current)
    return [Passage(source, index, item) for index, item in enumerate(passages)]


def query_terms(sector, sub_domains: list[str]) -> list[str]:
    selected = set(sub_domains)
    events = [item.event_type for item in sector.lifecycle.events if selected & set(item.sub_domains)] or list(sector.event_namespace)
    words = [sector.label]
    words += [name.replace("_", " ") for name in sub_domains]
    words += [event.replace(".", " ").replace("_", " ") for event in events]
    return tokens(" ".join(words))


def rank(passages: list[Passage], terms: list[str], k1: float = 1.5, b: float = 0.75) -> list[tuple[float, Passage]]:
    if not passages:
        return []
    documents = [Counter(tokens(item.text)) for item in passages]
    lengths = [sum(counts.values()) for counts in documents]
    average = (sum(lengths) / len(lengths)) or 1.0
    frequency = Counter(term for counts in documents for term in counts)
    wanted = Counter(terms)
    scored = []
    for passage, counts, length in zip(passages, documents, lengths):
        score = 0.0
        for term, weight in wanted.items():
            seen = counts.get(term, 0)
            if not seen:
                continue
            idf = math.log(1 + (len(passages) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            score += weight * idf * seen * (k1 + 1) / (seen + k1 * (1 - b + b * length / average))
        scored.append((round(score, 4), passage))
    return scored


def reference(items: list, sector, sub_domains: list[str], budget: int = 6000) -> tuple[str, list[dict]]:
    """The judge's warm-start reference within the budget, and which passages it holds."""
    docs = [doc for doc in (read_document(item) for item in ordered(items)) if doc.readable]
    # Passages stay a fraction of the budget, so a small budget still holds more than one.
    size = min(PASSAGE_CHARS, max(200, budget // 3))
    passages = [passage for doc in docs for passage in split_passages(doc.name, doc.body, size)]
    scored = rank(passages, query_terms(sector, sub_domains))
    chosen: list[tuple[float, Passage]] = []
    used = 0
    seen: set[str] = set()
    # Relevant passages first; when nothing matches, the opening passage of each document.
    candidates = sorted((item for item in scored if item[0] > 0), key=lambda item: -item[0]) or [
        (0.0, passage) for passage in passages if passage.index == 0
    ]
    for score, passage in candidates:
        if passage.text in seen or used + len(passage.text) > budget:
            continue
        seen.add(passage.text)
        chosen.append((score, passage))
        used += len(passage.text) + len(passage.source) + 4
    order = {doc.name: position for position, doc in enumerate(docs)}
    chosen.sort(key=lambda item: (order.get(item[1].source, 0), item[1].index))
    text = "\n\n".join(f"[{passage.source}] {passage.text}" for _, passage in chosen)
    return text, [{"source": passage.source, "passage": passage.index, "score": score, "characters": len(passage.text)} for score, passage in chosen]
