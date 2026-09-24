"""Read warm-start documents into a short, scrubbed excerpt."""

from __future__ import annotations

from pathlib import Path

from sectors.banking.corpus import scrub_text


def read_corpus_excerpt(items: list, limit: int = 2000) -> str:
    parts: list[str] = []
    for item in items:
        kind = getattr(item, "kind", "") or ""
        name = getattr(item, "name", "") or ""
        uri = getattr(item, "uri", None) or ""
        body = _read_file(getattr(item, "storage_path", None))
        text = scrub_text("\n".join(piece for piece in (f"{kind}: {name}", uri, body) if piece)).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)[:limit]


def _read_file(path: str | None) -> str:
    if not path:
        return ""
    file = Path(path)
    if not file.is_file():
        return ""
    raw = file.read_bytes()[:12000]
    if b"\x00" in raw[:512]:
        return ""
    return raw.decode("utf-8", errors="replace")
