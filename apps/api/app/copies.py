"""Keep uploaded records out of exports: a record that repeats 12 words in a row from any upload is left out.

The guard indexes every 12-word run of each document a study holds, and of each data-source row long
enough to have one, such as a complaint narrative. The exporter asks it about every record it writes; a
record with a matching run is left out, and the manifest counts what was left out, by part. Words are
compared lowercased, so a copy is found whatever its punctuation or case.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import select

from app.corpus_text import READ_BYTES, parse_file
from app.models import CorpusItem

WINDOW = 12
# At most this many runs are indexed per export; beyond it the manifest says the index was truncated.
MAX_WINDOWS = 1_000_000
MAX_ROWS = 500_000
WORD = re.compile(r"[^\W_]+")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


class CopyGuard:
    def __init__(self) -> None:
        self.windows: set[int] = set()
        self.documents = 0
        self.rows = 0
        self.truncated = False
        self.left_out: dict[str, int] = {}
        self._checked: dict[str, bool] = {}

    def index(self, text: str) -> None:
        found = words(text)
        for start in range(len(found) - WINDOW + 1):
            if len(self.windows) >= MAX_WINDOWS:
                self.truncated = True
                return
            self.windows.add(hash(tuple(found[start : start + WINDOW])))

    def _copied_text(self, text: str) -> bool:
        found = self._checked.get(text)
        if found is None:
            tokens = words(text)
            found = any(hash(tuple(tokens[start : start + WINDOW])) in self.windows for start in range(len(tokens) - WINDOW + 1))
            # Templated text repeats across records, so each distinct string is checked once.
            if len(self._checked) < 200_000:
                self._checked[text] = found
        return found

    def copied(self, value) -> bool:
        """Whether any text in a record, however deeply nested, repeats a run of an upload."""
        if not self.windows:
            return False
        if isinstance(value, str):
            return len(value) >= 2 * WINDOW and self._copied_text(value)
        if isinstance(value, dict):
            return any(self.copied(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(self.copied(item) for item in value)
        return False

    def leave_out(self, part: str, count: int = 1) -> None:
        self.left_out[part] = self.left_out.get(part, 0) + count

    def report(self) -> dict:
        return {
            "window_words": WINDOW,
            "documents": self.documents,
            "data_source_rows": self.rows,
            "windows": len(self.windows),
            "truncated": self.truncated,
            "left_out": dict(sorted(self.left_out.items())),
        }


def _rows(path: Path) -> Iterator[str]:
    """A data source's rows as text: CSV and XES lines, plain or gzipped, or Parquet rows when pyarrow is there."""
    with path.open("rb") as handle:
        head = handle.read(4)
    if head.startswith(b"PAR1"):
        try:
            import pyarrow.parquet as parquet
        except ImportError:
            return
        for row in parquet.read_table(path).to_pylist()[:MAX_ROWS]:
            yield " ".join(str(value) for value in row.values() if value is not None)
        return
    opener = gzip.open if head.startswith(b"\x1f\x8b") else open
    read = 0
    with opener(path, "rt", encoding="utf-8", errors="ignore") as handle:
        for number, line in enumerate(handle):
            read += len(line)
            if read > READ_BYTES or number >= MAX_ROWS:
                return
            yield line


def guard_for(db, run) -> CopyGuard:
    """Index the uploads of the study a run belongs to: documents, fetched links, deep-search reports, and data sources."""
    guard = CopyGuard()
    for item in db.scalars(select(CorpusItem).where(CorpusItem.project_id == run.project_id)):
        path = Path(item.storage_path) if item.storage_path else None
        if path is None or not path.is_file():
            continue
        if item.kind == "data_source":
            for row in _rows(path):
                if len(words(row)) >= WINDOW:
                    guard.index(row)
                    guard.rows += 1
            continue
        parsed = parse_file(str(path))
        if parsed.readable:
            guard.index(parsed.text)
            guard.documents += 1
    return guard
