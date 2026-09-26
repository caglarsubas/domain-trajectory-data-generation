"""Calibrate a study from its data sources, as the `calibrate` job runs it.

An event log's activities are mapped to the pack's event types: by the catalogue when the source comes
from it, by what a person saved, or by a suggestion from shared words. The mapped cases become a
calibration of next-step counts and durations. Two known exports have adapters instead: UCI Bank
Marketing (channel and conversion) and the CFPB complaint database (channel and outcome shares).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.catalogue import BY_ID
from app.eventlog import LogError, peek_csv_header, read_cases
from app.models import CorpusItem, Project
from sectors.calibration import Calibration, build
from sectors.registry import get_sector

SYNONYMS = {
    "create": "started", "created": "started", "start": "started", "begin": "started", "new": "started",
    "submit": "submitted", "submission": "submitted", "sent": "submitted",
    "accept": "approved", "accepted": "approved", "approve": "approved", "granted": "approved",
    "deny": "declined", "denied": "declined", "decline": "declined", "reject": "declined", "rejected": "declined", "refused": "declined",
    "cancel": "abandoned", "cancelled": "abandoned", "canceled": "abandoned", "withdrawn": "abandoned", "abandon": "abandoned",
    "open": "opened", "fund": "funded", "deposit": "funded", "issue": "issued", "activate": "activated",
    "disburse": "disbursed", "payout": "disbursed", "repay": "repayment", "repaid": "repayment", "close": "closed",
    "complain": "complaint", "resolve": "resolved", "incomplete": "review", "verify": "kyc", "verification": "kyc", "identity": "kyc",
}
PREFIX = re.compile(r"^[a-z]_")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", PREFIX.sub("", text.lower()))
    return {SYNONYMS.get(word, word) for word in words}


def suggest(activities: list[str], namespace: tuple[str, ...]) -> dict[str, str | None]:
    """Each activity's best event by shared words, or None when nothing fits or two events tie."""
    events = {event: set(re.split(r"[._]", event)) for event in namespace}
    mapping: dict[str, str | None] = {}
    for activity in activities:
        words = _tokens(activity)
        scored = sorted(((len(words & parts) / len(parts), event) for event, parts in events.items()), reverse=True)
        best = scored[0] if scored else (0.0, None)
        tie = len(scored) > 1 and scored[1][0] == best[0]
        mapping[activity] = best[1] if best[0] >= 0.5 and not tie else None
    return mapping


def _recognize(path: Path) -> str | None:
    with path.open("rb") as handle:
        head = handle.read(65536)
    if head.startswith(b"PK\x03\x04"):
        return "uci_bank_marketing" if b"bank" in head else None
    # Only text can be a CSV export; compressed, XML, JSON, and Parquet logs go to the event-log readers.
    if head.startswith((b"\x1f\x8b", b"PAR1")) or head.lstrip()[:1] in {b"<", b"{"} or b"\x00" in head[:1024]:
        return None
    try:
        header = {name.strip().lower() for name in peek_csv_header(head)}
    except csv.Error:
        return None
    if {"date received", "submitted via", "company response to consumer"} <= header:
        return "cfpb_complaints"
    return None


def calibrate_item(db: Session, item: CorpusItem, progress=None) -> dict:
    project = db.get(Project, item.project_id)
    sector = get_sector(project.sector)
    namespace = tuple(sector.event_namespace)
    path = Path(item.storage_path or "")
    if not path.is_file():
        raise HTTPException(status_code=409, detail="the data source has no stored file yet")
    catalogue = (item.ingest or {}).get("catalogue")
    adapter = catalogue if catalogue in {"uci_bank_marketing", "cfpb_complaints"} else _recognize(path)
    if progress is not None:
        progress(0, 2, "Reading the data source.")
    previous = item.calibration or {}
    try:
        if adapter == "uci_bank_marketing":
            result = _bank_marketing(path)
        elif adapter == "cfpb_complaints":
            result = _complaints(path)
        else:
            result = _event_log(path, namespace, previous.get("mapping"), BY_ID.get(catalogue, {}).get("mapping"), progress)
    except LogError as exc:
        item.calibration = {"status": "failed", "reason": str(exc)}
        db.commit()
        raise HTTPException(status_code=422, detail=f"The data source could not be read as an event log: {exc}") from exc
    result["status"] = "ready"
    item.calibration = result
    db.commit()
    return {"id": item.id, "cases": result["cases"], "mapped_share": result.get("mapped_share"), "format": result["format"]}


def _event_log(path: Path, namespace: tuple[str, ...], saved: dict | None, catalogue: dict | None, progress) -> dict:
    kind, cases = read_cases(path)
    activities = Counter()
    total_cases = 0
    for case in cases:
        total_cases += 1
        activities.update(name for name, _ in case)
    if not activities:
        raise LogError("the log has no events")
    suggested = suggest(list(activities), namespace)
    mapping = {activity: (saved or {}).get(activity, (catalogue or {}).get(activity, suggested[activity])) for activity in activities}
    mapping = {activity: event if event in namespace else None for activity, event in mapping.items()}
    if progress is not None:
        progress(1, 2, "Counting steps and durations.")
    _, cases = read_cases(path)

    def mapped():
        for case in cases:
            start = next((when for _, when in case if when is not None), None)
            yield [(mapping[name], None if when is None or start is None else (when - start) / 3600.0) for name, when in case if mapping.get(name)]

    calibration = build(mapped(), source=path.name)
    events = sum(activities.values())
    mapped_events = sum(count for name, count in activities.items() if mapping.get(name))
    return {
        "format": kind,
        "cases": total_cases,
        "events": events,
        "activities": dict(activities.most_common(300)),
        "mapping": mapping,
        "mapped_share": round(mapped_events / events, 3) if events else 0.0,
        "calibration": calibration.as_dict(),
        "channels": {},
    }


def _read_zip_csv(path: Path, preferred: tuple[str, ...]) -> tuple[str, str]:
    """The first preferred CSV inside a zip, looking one zip deep, as the UCI archive nests its files."""
    archive = zipfile.ZipFile(path)
    for name in preferred:
        for member in archive.namelist():
            if member.endswith(name):
                return member, archive.read(member).decode("utf-8", errors="replace")
    for member in archive.namelist():
        if member.endswith(".zip"):
            inner = zipfile.ZipFile(io.BytesIO(archive.read(member)))
            for name in preferred:
                for nested in inner.namelist():
                    if nested.endswith(name):
                        return nested, inner.read(nested).decode("utf-8", errors="replace")
    raise LogError("the archive has none of the expected CSV files")


def _bank_marketing(path: Path) -> dict:
    name, text = _read_zip_csv(path, ("bank-additional-full.csv", "bank-full.csv", "bank.csv"))
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    if not rows or "y" not in rows[0]:
        raise LogError("the UCI file has no outcome column")
    yes = sum(1 for row in rows if row["y"].strip().strip('"') == "yes")
    calibration = Calibration(sources=[name])
    calibration.cases = len(rows)
    calibration.starts["product.viewed"] = len(rows)
    # An offer that converts goes on to an application; the others end after the view.
    calibration.transitions["product.viewed"] = Counter({"application.started": yes})
    calibration.ends.update({"product.viewed": len(rows) - yes, "application.started": yes})
    return {
        "format": "UCI Bank Marketing",
        "cases": len(rows),
        "events": len(rows),
        "activities": {"campaign contact": len(rows), "term deposit subscribed": yes},
        "mapping": {},
        "mapped_share": 1.0,
        "calibration": calibration.as_dict(),
        # Both contact types, cellular and telephone, are calls from the bank.
        "channels": {"call_centre": len(rows)},
        "outcomes": {"converted": round(yes / len(rows), 4)},
    }


CFPB_CHANNELS = {"web": "web", "email": "web", "web referral": "web", "phone": "call_centre", "referral": "branch", "postal mail": "branch", "fax": "branch"}
CFPB_RELIEF = {"closed with monetary relief", "closed with non-monetary relief", "closed with relief"}
CFPB_CLOSED = {"closed with explanation", "closed", "closed without relief"}


def _complaints(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise LogError("the complaint export is empty")
    lower = {key.strip().lower(): key for key in rows[0]}
    channel_column, response_column = lower.get("submitted via"), lower.get("company response to consumer")
    channels, relief, closed = Counter(), 0, 0
    for row in rows:
        channel = CFPB_CHANNELS.get((row.get(channel_column) or "").strip().lower())
        if channel:
            channels[channel] += 1
        response = (row.get(response_column) or "").strip().lower()
        relief += response in CFPB_RELIEF
        closed += response in CFPB_CLOSED
    calibration = Calibration(sources=[path.name])
    calibration.cases = len(rows)
    calibration.starts["complaint.received"] = len(rows)
    calibration.transitions["complaint.received"] = Counter({"complaint.resolved": relief, "complaint.rejected": closed})
    calibration.ends.update({"complaint.resolved": relief, "complaint.rejected": closed})
    return {
        "format": "CFPB complaint export",
        "cases": len(rows),
        "events": len(rows),
        "activities": {"complaint": len(rows), "closed with relief": relief, "closed with explanation": closed},
        "mapping": {},
        "mapped_share": 1.0,
        "calibration": calibration.as_dict(),
        "channels": dict(channels),
        "outcomes": {"relief": round(relief / len(rows), 4), "explanation": round(closed / len(rows), 4)},
    }


def study_calibration(items: list) -> tuple[Calibration | None, dict]:
    """Every ready data source of a study as one calibration, and the channel counts they carry."""
    from sectors.calibration import merge

    ready = [item for item in items if item.kind == "data_source" and (item.calibration or {}).get("status") == "ready"]
    if not ready:
        return None, {}
    parts = []
    channels = Counter()
    for item in ready:
        part = Calibration.from_dict(item.calibration["calibration"])
        part.sources = [item.name]
        parts.append(part)
        channels.update(item.calibration.get("channels") or {})
    return merge(parts), dict(channels)
