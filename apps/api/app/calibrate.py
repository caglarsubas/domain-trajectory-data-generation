"""Calibrate a study from its data sources, as the `calibrate` job runs it.

An event log's activities are mapped to the pack's event types by what a person saved, else by the
catalogue's mapping when the source comes from it, which leaves an activity it omits unmapped, or, for an
upload, by a suggestion from shared words. The mapped cases become a calibration of next-step counts and
durations. Known exports have adapters instead: UCI Bank Marketing (channel and conversion), the CFPB
complaint database (channel and outcome shares), the hotel booking demand datasets (a reservation's
changes, cancellation, and arrival), and BTS on-time performance (a flight's delay, cancellation, and
arrival).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter
from datetime import date, timedelta
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
    """Each activity's best event by shared words, or None when nothing fits or two events tie. An activity named exactly
    as an event, as the pack's log template writes them, is that event."""
    events = {event: set(re.split(r"[._]", event)) for event in namespace}
    mapping: dict[str, str | None] = {}
    for activity in activities:
        if activity.strip() in events:
            mapping[activity] = activity.strip()
            continue
        words = _tokens(activity)
        scored = sorted(((len(words & parts) / len(parts), event) for event, parts in events.items()), reverse=True)
        best = scored[0] if scored else (0.0, None)
        tie = len(scored) > 1 and scored[1][0] == best[0]
        mapping[activity] = best[1] if best[0] >= 0.5 and not tie else None
    return mapping


ADAPTERS = ("uci_bank_marketing", "cfpb_complaints", "hotel_booking_demand", "bts_on_time")


def _column(name: str) -> str:
    """A column name as the adapters compare it: `DEP_DEL15`, `DepDel15`, and `dep_del15` are one column."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


HOTEL_COLUMNS = {"iscanceled", "leadtime", "arrivaldateyear", "arrivaldatemonth", "arrivaldatedayofmonth", "reservationstatus", "reservationstatusdate"}
BTS_COLUMNS = {"cancelled", "diverted", "depdelay", "depdel15", "arrdel15", "actualelapsedtime"}


def _recognize(path: Path) -> str | None:
    with path.open("rb") as handle:
        head = handle.read(65536)
    if head.startswith(b"PK\x03\x04"):
        if b"On_Time" in head:
            return "bts_on_time"
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
    columns = {_column(name) for name in header}
    if HOTEL_COLUMNS <= columns:
        return "hotel_booking_demand"
    if BTS_COLUMNS <= columns:
        return "bts_on_time"
    return None


def calibrate_item(db: Session, item: CorpusItem, progress=None) -> dict:
    project = db.get(Project, item.project_id)
    sector = get_sector(project.sector)
    namespace = tuple(sector.event_namespace)
    path = Path(item.storage_path or "")
    if not path.is_file():
        raise HTTPException(status_code=409, detail="the data source has no stored file yet")
    catalogue = (item.ingest or {}).get("catalogue")
    adapter = catalogue if catalogue in ADAPTERS else _recognize(path)
    if progress is not None:
        progress(0, 2, "Reading the data source.")
    try:
        if adapter == "uci_bank_marketing":
            result = _bank_marketing(path)
        elif adapter == "cfpb_complaints":
            result = _complaints(path)
        elif adapter == "hotel_booking_demand":
            result = _hotel_bookings(path)
        elif adapter == "bts_on_time":
            result = _on_time(path)
        else:
            saved = saved_mapping(item, namespace)
            result = _event_log(path, namespace, saved, BY_ID.get(catalogue, {}).get("mapping"), progress)
            result["saved"] = saved
    except LogError as exc:
        item.calibration = {"status": "failed", "reason": str(exc)}
        db.commit()
        raise HTTPException(status_code=422, detail=f"The data source could not be read as an event log: {exc}") from exc
    result["status"] = "ready"
    if result.get("calibration"):
        from app.log_template import preview

        if progress is not None:
            progress(1, 2, "Previewing what calibration changes.")
        # What the calibration would change in a run, before one is made (Slice 15).
        result["preview"] = preview(sector, Calibration.from_dict(result["calibration"]))
    item.calibration = result
    db.commit()
    return {"id": item.id, "cases": result["cases"], "mapped_share": result.get("mapped_share"), "format": result["format"]}


def saved_mapping(item: CorpusItem, namespace: tuple[str, ...]) -> dict[str, str | None]:
    """The events a person saved for a data source's activities.

    Calibrations from before these were kept apart hold only the mapping in use, catalogue entries and suggestions
    included. There, an activity counts as saved where that mapping differs from what they gave it, then or now.
    """
    calibration = item.calibration or {}
    if "saved" in calibration:
        return dict(calibration["saved"])
    used = calibration.get("mapping") or {}
    entry = BY_ID.get((item.ingest or {}).get("catalogue"), {})
    catalogue, retired = entry.get("mapping") or {}, entry.get("retired_mapping") or {}
    suggested = suggest(list(used), namespace)
    given = {activity: catalogue.get(activity, suggested[activity]) for activity in used}
    return {
        activity: event
        for activity, event in used.items()
        if event != (given[activity] if given[activity] in namespace else None) and (activity, event) not in retired.items()
    }


def _event_log(path: Path, namespace: tuple[str, ...], saved: dict | None, catalogue: dict | None, progress) -> dict:
    kind, cases = read_cases(path)
    activities = Counter()
    total_cases = 0
    for case in cases:
        total_cases += 1
        activities.update(name for name, _ in case)
    if not activities:
        raise LogError("the log has no events")
    # A catalogue source's mapping is the whole of it: an activity it leaves out has no event of its own in the pack,
    # so it stays unmapped unless a person maps it. Only an upload falls back on shared words.
    suggested = suggest(list(activities), namespace) if catalogue is None else {}
    mapping = {activity: (saved or {}).get(activity, (catalogue or {}).get(activity, suggested.get(activity))) for activity in activities}
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


MONTHS = {name: number for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), start=1
)}


def _number(value: str) -> float:
    return float(value.strip() or "0")


def _hotel_bookings(path: Path) -> dict:
    """Each booking as a reservation's path from confirmation, timed in hours from the booking date.

    The data holds only bookings that were made, so it cannot say how often a guarantee is declined or a reservation
    lapses; those events stay out and keep the pack's weights. A change is known to have happened but not when.
    """
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = {_column(name): name for name in reader.fieldnames or []}
        missing = HOTEL_COLUMNS - set(columns)
        if missing:
            raise LogError(f"the booking file has no {', '.join(sorted(missing))} column")
        statuses, terms = Counter(), Counter()
        tally = {"changed": 0, "skipped": 0, "events": 0}

        def get(row: dict, key: str) -> str:
            return (row.get(columns.get(key, ""), "") or "").strip()

        def cases():
            for row in reader:
                try:
                    arrival = date(int(get(row, "arrivaldateyear")), MONTHS[get(row, "arrivaldatemonth").lower()], int(get(row, "arrivaldatedayofmonth")))
                    booked = arrival - timedelta(days=int(_number(get(row, "leadtime"))))
                    closed = date.fromisoformat(get(row, "reservationstatusdate")[:10])
                    nights = int(_number(get(row, "staysinweekendnights"))) + int(_number(get(row, "staysinweeknights")))
                    changes = int(_number(get(row, "bookingchanges")))
                except (KeyError, ValueError):
                    tally["skipped"] += 1
                    continue
                status = get(row, "reservationstatus").lower()
                at_arrival = (arrival - booked).days * 24.0
                steps: list[tuple[str, float | None]] = [("reservation.confirmed", 0.0)]
                if changes > 0:
                    tally["changed"] += 1
                    steps += [("modification.requested", None), ("reservation.modified", None)]
                if status == "canceled":
                    steps.append(("reservation.cancelled", (closed - booked).days * 24.0))
                    # A kept deposit is a cancellation fee and a refundable one is refunded; without a deposit there is neither.
                    deposit = get(row, "deposittype").lower()
                    if deposit == "non refund":
                        steps.append(("cancellation.fee_charged", None))
                    elif deposit == "refundable":
                        steps.append(("refund.issued", None))
                    terms[deposit or "unknown"] += 1
                elif status == "no-show":
                    steps += [("room.assigned", at_arrival), ("guest.no_show", None)]
                elif status == "check-out":
                    steps += [("room.assigned", at_arrival), ("guest.checked_in", at_arrival), ("guest.checked_out", at_arrival + nights * 24.0)]
                else:
                    tally["skipped"] += 1
                    continue
                statuses[status] += 1
                tally["events"] += len(steps)
                yield steps

        calibration = build(cases(), source=path.name)
    total = sum(statuses.values())
    if not total:
        raise LogError("the booking file has no bookings with a known final status")
    return {
        "format": "Hotel booking demand",
        "cases": total,
        "events": tally["events"],
        "activities": {"checked out": statuses["check-out"], "cancelled": statuses["canceled"], "no-show": statuses["no-show"], "changed": tally["changed"]},
        "mapping": {},
        "mapped_share": 1.0,
        "calibration": calibration.as_dict(),
        "channels": {},
        "outcomes": {
            "cancelled": round(statuses["canceled"] / total, 4),
            "no_show": round(statuses["no-show"] / total, 4),
            "arrived": round(statuses["check-out"] / total, 4),
            "changed": round(tally["changed"] / total, 4),
        },
        "skipped": tally["skipped"],
    }


def _open_rows(path: Path):
    """The rows of a CSV, or of the first CSV inside a zip, streamed: a month of flights is over 200 MB unpacked."""
    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        member = next((name for name in archive.namelist() if name.lower().endswith(".csv")), None)
        if member is None:
            raise LogError("the archive holds no CSV file")
        return member, io.TextIOWrapper(archive.open(member), encoding="utf-8-sig", errors="replace", newline="")
    return path.name, path.open("r", encoding="utf-8-sig", errors="replace", newline="")


def _on_time(path: Path) -> dict:
    """Each flight as a checked-in passenger's path to arrival, timed in hours from its scheduled departure.

    BTS measures a delay at the gate: the actual departure against the scheduled one. So a delay comes between boarding
    and departure, and runs from the scheduled departure to the actual one; a flight whose departure or arrival was 15
    minutes late or more is a delayed flight. Its arrival then follows the delay, which lets second-order shares tell a
    delayed flight's arrival from an on-time one's. Diverted flights are left out.
    """
    name, handle = _open_rows(path)
    with handle:
        reader = csv.DictReader(handle)
        columns = {_column(name): name for name in reader.fieldnames or []}
        missing = BTS_COLUMNS - set(columns)
        if missing:
            raise LogError(f"the flight file has no {', '.join(sorted(missing))} column")
        counts = Counter()

        def get(row: dict, key: str) -> str:
            return (row.get(columns[key], "") or "").strip()

        def flag(row: dict, key: str) -> bool:
            value = get(row, key)
            return bool(value) and float(value) >= 1

        def cases():
            for row in reader:
                try:
                    if flag(row, "cancelled"):
                        counts["cancelled"] += 1
                        yield [("passenger.checked_in", None), ("flight.cancelled", None)]
                        continue
                    if flag(row, "diverted"):
                        counts["diverted"] += 1
                        continue
                    departure = max(float(get(row, "depdelay")), 0.0) / 60.0
                    arrival = departure + float(get(row, "actualelapsedtime")) / 60.0
                    late_departure, late_arrival = flag(row, "depdel15"), flag(row, "arrdel15")
                except ValueError:
                    counts["skipped"] += 1
                    continue
                if late_departure or late_arrival:
                    counts["delayed"] += 1
                    counts["arrived_late"] += late_arrival
                    yield [
                        ("passenger.checked_in", None),
                        ("passenger.boarded", None),
                        ("flight.delayed", 0.0),
                        ("flight.departed", departure),
                        ("flight.arrived_late" if late_arrival else "flight.arrived", arrival),
                    ]
                else:
                    counts["on_time"] += 1
                    yield [("passenger.checked_in", None), ("passenger.boarded", None), ("flight.departed", departure), ("flight.arrived", arrival)]

        calibration = build(cases(), source=name)
    flown = counts["on_time"] + counts["delayed"]
    total = flown + counts["cancelled"]
    if not total:
        raise LogError("the flight file has no flights")
    return {
        "format": "BTS on-time performance",
        "cases": total,
        "events": counts["cancelled"] * 2 + counts["on_time"] * 4 + counts["delayed"] * 5,
        "activities": {"on time": counts["on_time"], "delayed": counts["delayed"], "cancelled": counts["cancelled"], "diverted, left out": counts["diverted"]},
        "mapping": {},
        "mapped_share": 1.0,
        "calibration": calibration.as_dict(),
        "channels": {},
        "outcomes": {
            "delayed": round(counts["delayed"] / total, 4),
            "cancelled": round(counts["cancelled"] / total, 4),
            "arrived_late_when_delayed": round(counts["arrived_late"] / counts["delayed"], 4) if counts["delayed"] else None,
        },
        "skipped": counts["skipped"],
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
