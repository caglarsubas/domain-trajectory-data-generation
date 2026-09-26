"""Store what a fetched link returned, and parse it once, as the `fetch` job runs it."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import runtime
from app.corpus_text import parse_file
from app.fetch import FetchError, fetch_link
from app.models import CorpusItem
from app.settings import Settings

SUFFIXES = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "application/json": ".json",
    "application/yaml": ".yaml",
    "application/x-yaml": ".yaml",
    "text/yaml": ".yaml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def safe_name(name: str | None) -> str:
    """A file name with no directory part and no characters a path could misread."""
    base = Path(name or "").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")[:120]
    return cleaned or "document"


def _suffix(content_type: str, url: str) -> str:
    kind = content_type.split(";")[0].strip().lower()
    if kind in SUFFIXES:
        return SUFFIXES[kind]
    guessed = Path(urlsplit(url).path).suffix.lower()
    return guessed if guessed in set(SUFFIXES.values()) | {".yml", ".markdown"} else (mimetypes.guess_extension(kind) or ".txt")


def download_into_corpus(db: Session, item: CorpusItem, cfg: Settings, progress=None) -> dict:
    """A catalogue source streamed to the upload store, with its licence, origin, and snapshot date."""
    from app.catalogue import BY_ID, MAX_DOWNLOAD
    from app.fetch import safe_download

    entry = BY_ID[item.ingest["catalogue"]]
    if progress is not None:
        progress(0, 2, f"Downloading {entry['name']} ({entry.get('bytes', 0) / 1_000_000:.0f} MB).")
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    partial = cfg.upload_dir / f"{item.id}.partial"
    download = runtime.fetcher.download if runtime.fetcher is not None else safe_download
    try:
        final, content_type, size, digest = download(entry["url"], partial, max_bytes=MAX_DOWNLOAD)
    except FetchError as exc:
        partial.unlink(missing_ok=True)
        item.ingest = {**item.ingest, "status": "failed", "detail": str(exc), "at": datetime.now(timezone.utc).isoformat()}
        db.commit()
        raise HTTPException(status_code=422, detail=f"{entry['name']} could not be downloaded: {exc}") from exc
    path = cfg.upload_dir / f"{digest}-{safe_name(entry['file'])}"
    partial.replace(path)
    item.storage_path = str(path)
    item.content_hash = digest
    item.ingest = {
        **item.ingest,
        "status": "fetched",
        "final_url": final,
        "content_type": content_type,
        "bytes": size,
        "content_hash": digest,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "detail": f"{entry['name']}, {size / 1_000_000:.1f} MB",
    }
    db.commit()
    return {"id": item.id, "readable": True, "parser": "event log", "detail": item.ingest["detail"]}


def fetch_into_corpus(db: Session, item: CorpusItem, cfg: Settings, progress=None) -> dict:
    if (item.ingest or {}).get("catalogue"):
        return download_into_corpus(db, item, cfg, progress)
    if progress is not None:
        progress(0, 2, f"Fetching {item.uri}.")
    try:
        found = runtime.fetcher.fetch(item.uri, item.kind) if runtime.fetcher is not None else fetch_link(item.uri, item.kind)
    except FetchError as exc:
        item.ingest = {"status": "failed", "detail": str(exc), "at": datetime.now(timezone.utc).isoformat()}
        db.commit()
        raise HTTPException(status_code=422, detail=f"The link could not be fetched: {exc}") from exc
    digest = hashlib.sha256(found.body).hexdigest()
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.upload_dir / f"{digest}-{safe_name(item.name)}{_suffix(found.content_type, found.url)}"
    path.write_bytes(found.body)
    item.storage_path = str(path)
    if progress is not None:
        progress(1, 2, "Reading it.")
    parsed = parse_file(str(path))
    item.ingest = {
        "status": "fetched",
        "final_url": found.url,
        "content_type": found.content_type,
        "bytes": len(found.body),
        "content_hash": digest,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": found.parser_hint or None,
        "detail": found.detail or parsed.detail,
        "sources": found.sources[:30],
    }
    db.commit()
    return {"id": item.id, "readable": parsed.readable, "parser": found.parser_hint or parsed.parser, "detail": item.ingest["detail"]}
