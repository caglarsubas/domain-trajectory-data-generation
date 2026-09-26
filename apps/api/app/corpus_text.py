"""Read warm-start documents: one scrubbed text per document, parsed once and cached beside the file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.documents import PARSER_VERSION, Parsed, parse_bytes
from sectors.steering import scrub_text

# Stored files are at most 20 MB; that is also the most a parser is handed.
READ_BYTES = 20_000_000


@dataclass(frozen=True)
class DocumentText:
    item_id: str
    kind: str
    name: str
    text: str
    readable: bool
    reason: str | None
    parser: str = ""
    detail: str = ""
    # The document's own text, scrubbed, without the kind and name header steering reads.
    body: str = ""


def ordered(items: list) -> list:
    """Documents in upload order, so steering and seeds never depend on database row order."""
    return sorted(items, key=lambda item: (getattr(item, "created_at", None) or 0, getattr(item, "id", "")))


def read_document(item) -> DocumentText:
    kind = getattr(item, "kind", "") or ""
    name = getattr(item, "name", "") or ""
    uri = getattr(item, "uri", None) or ""
    path = getattr(item, "storage_path", None)
    body = ""
    if path:
        parsed = parse_file(path)
        body = _scrubbed(path) if parsed.readable else ""
    else:
        ingest = getattr(item, "ingest", None) or {}
        if ingest.get("status") == "failed":
            reason = f"The link could not be fetched: {ingest.get('detail') or 'unknown error'}"
        else:
            reason = "The link has not been fetched yet."
        parsed = Parsed("", "link", "", reason)
    header = scrub_text("\n".join(piece for piece in (f"{kind}: {name}", uri) if piece))
    text = "\n".join(piece for piece in (header, body) if piece).strip()
    return DocumentText(getattr(item, "id", ""), kind, name, text, parsed.readable, parsed.reason, parsed.parser, parsed.detail, body)


def parse_file(path: str) -> Parsed:
    file = Path(path)
    if not file.is_file():
        return Parsed("", "file", "", "The stored file is missing.")
    return _parse_cached(str(file), file.stat().st_mtime)


def _scrubbed(path: str) -> str:
    file = Path(path)
    return _scrub_cached(str(file), file.stat().st_mtime)


@lru_cache(maxsize=64)
def _scrub_cached(path: str, mtime: float) -> str:
    return scrub_text(_parse_cached(path, mtime).text).strip()


def sidecar(path: str | Path) -> Path:
    return Path(f"{path}.text.json")


@lru_cache(maxsize=64)
def _parse_cached(path: str, mtime: float) -> Parsed:
    cache = sidecar(path)
    try:
        if cache.is_file() and cache.stat().st_mtime >= mtime:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == PARSER_VERSION:
                return Parsed(data["text"], data["parser"], data["detail"], data.get("reason"))
    except (OSError, ValueError, KeyError):
        pass
    raw = Path(path).read_bytes()[:READ_BYTES]
    parsed = parse_bytes(raw, name=Path(path).name)
    try:
        temporary = cache.with_name(cache.name + ".tmp")
        temporary.write_text(
            json.dumps({"version": PARSER_VERSION, "text": parsed.text, "parser": parsed.parser, "detail": parsed.detail, "reason": parsed.reason}),
            encoding="utf-8",
        )
        os.replace(temporary, cache)
    except OSError:
        pass
    return parsed


def read_corpus_text(items: list) -> str:
    """Every readable document in full, for steering the generator."""
    return "\n\n".join(doc.text for doc in (read_document(item) for item in ordered(items)) if doc.text)
