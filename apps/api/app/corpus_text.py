"""Read warm-start documents: one scrubbed text per document, and a short excerpt for the judge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sectors.steering import scrub_text

READ_BYTES = 12000


@dataclass(frozen=True)
class DocumentText:
    item_id: str
    kind: str
    name: str
    text: str
    readable: bool
    reason: str | None


def ordered(items: list) -> list:
    """Documents in upload order, so steering and seeds never depend on database row order."""
    return sorted(items, key=lambda item: (getattr(item, "created_at", None) or 0, getattr(item, "id", "")))


def read_document(item) -> DocumentText:
    kind = getattr(item, "kind", "") or ""
    name = getattr(item, "name", "") or ""
    uri = getattr(item, "uri", None) or ""
    body, reason = _read_file(getattr(item, "storage_path", None))
    if not getattr(item, "storage_path", None):
        reason = "A link is stored but not fetched yet; fetching arrives in Slice 5."
    text = scrub_text("\n".join(piece for piece in (f"{kind}: {name}", uri, body) if piece)).strip()
    return DocumentText(getattr(item, "id", ""), kind, name, text, bool(body.strip()), reason)


def read_corpus_text(items: list) -> str:
    """Every readable document in full (up to the read limit), for steering the generator."""
    return "\n\n".join(doc.text for doc in (read_document(item) for item in ordered(items)) if doc.text)


def read_corpus_excerpt(items: list, limit: int = 2000) -> str:
    """A short excerpt for the judge brief."""
    return read_corpus_text(items)[:limit]


def _read_file(path: str | None) -> tuple[str, str | None]:
    if not path:
        return "", None
    file = Path(path)
    if not file.is_file():
        return "", "The stored file is missing."
    raw = file.read_bytes()[:READ_BYTES]
    if raw.startswith(b"%PDF"):
        return "", "PDF text is not extracted yet; PDF parsing arrives in Slice 5."
    if raw.startswith(b"PK\x03\x04"):
        return "", "Office and zip files are not read yet; parsing arrives in Slice 5."
    if b"\x00" in raw[:512]:
        return "", "Binary file; no text was read."
    text = raw.decode("utf-8", errors="replace")
    if text.count("�") > max(20, len(text) // 20):
        return "", "The file is not UTF-8 text."
    return text, None
