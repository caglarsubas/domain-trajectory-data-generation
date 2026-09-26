"""Read event logs as cases: CSV, Parquet, XES (optionally gzipped), and OCEL 2.0.

Each case is a list of (activity, seconds since the epoch or None), in time order. Readers stream where
the format allows, so a large log such as BPI Challenge 2017 is read without holding its XML in memory.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree

CASE_COLUMNS = ("case:concept:name", "case_id", "case id", "caseid", "case", "case:id", "application", "trace_id")
ACTIVITY_COLUMNS = ("concept:name", "activity", "activity name", "activity_name", "event", "event_type", "event name", "task")
TIME_COLUMNS = ("time:timestamp", "timestamp", "time", "complete timestamp", "complete_timestamp", "end_time", "start_time", "date", "datetime")
TIME_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%y")

Case = list[tuple[str, float | None]]


class LogError(Exception):
    """A file that is not an event log this reader understands, with a reason to show."""


def seconds(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value).strip()
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for pattern in TIME_FORMATS:
                try:
                    stamp = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
            else:
                return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def detect(path: Path) -> str:
    head = path.open("rb").read(4096)
    if head.startswith(b"PAR1"):
        return "parquet"
    if head.startswith(b"\x1f\x8b"):
        return "xes"
    stripped = head.lstrip()
    if stripped.startswith(b"<"):
        return "xes"
    if stripped.startswith(b"{"):
        return "ocel"
    return "csv"


def read_cases(path: str | Path) -> tuple[str, Iterator[Case]]:
    """The log's format and an iterator over its cases. Raises LogError when the file is no event log."""
    path = Path(path)
    kind = detect(path)
    reader = {"parquet": _parquet, "xes": _xes, "ocel": _ocel, "csv": _csv}[kind]
    return kind, reader(path)


def _pick(header: list[str], names: tuple[str, ...]) -> str | None:
    lowered = {name.strip().lower(): name for name in header}
    return next((lowered[name] for name in names if name in lowered), None)


def _sorted(cases: dict[str, Case]) -> Iterator[Case]:
    for events in cases.values():
        yield sorted(events, key=lambda item: (item[1] is None, item[1] or 0.0))


def _csv(path: Path) -> Iterator[Case]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(65536)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = csv.DictReader(handle, dialect=dialect)
        header = rows.fieldnames or []
        case, activity, time = _pick(header, CASE_COLUMNS), _pick(header, ACTIVITY_COLUMNS), _pick(header, TIME_COLUMNS)
        if not case or not activity:
            raise LogError("no case and activity columns were found; name them case_id and activity, or use XES or OCEL")
        cases: dict[str, Case] = defaultdict(list)
        for row in rows:
            if row.get(case) and row.get(activity):
                cases[row[case]].append((row[activity].strip(), seconds(row.get(time)) if time else None))
    return _sorted(cases)


def _parquet(path: Path) -> Iterator[Case]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise LogError("Parquet needs pyarrow, which is not installed on this server") from exc
    table = pq.read_table(path)
    case, activity, time = _pick(table.column_names, CASE_COLUMNS), _pick(table.column_names, ACTIVITY_COLUMNS), _pick(table.column_names, TIME_COLUMNS)
    if not case or not activity:
        raise LogError("no case and activity columns were found in the Parquet file")
    columns = table.select([name for name in (case, activity, time) if name]).to_pydict()
    cases: dict[str, Case] = defaultdict(list)
    times = columns.get(time) if time else None
    for index, key in enumerate(columns[case]):
        name = columns[activity][index]
        if key is not None and name:
            cases[str(key)].append((str(name), seconds(times[index]) if times else None))
    return _sorted(cases)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xes(path: Path) -> Iterator[Case]:
    with path.open("rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    return _xes_cases(gzip.open(path, "rb") if compressed else path.open("rb"))


def _xes_cases(stream) -> Iterator[Case]:
    try:
        events: Case = []
        current: dict[str, str] = {}
        in_event = False
        seen_log = False
        for action, element in ElementTree.iterparse(stream, events=("start", "end")):
            tag = _local(element.tag)
            if action == "start":
                if tag == "log":
                    seen_log = True
                elif tag == "trace":
                    events = []
                elif tag == "event":
                    in_event, current = True, {}
                continue
            if tag in {"string", "date"} and in_event:
                current[element.get("key", "")] = element.get("value", "")
            elif tag == "event":
                in_event = False
                transition = current.get("lifecycle:transition", "complete").lower()
                if current.get("concept:name") and transition in {"complete", ""}:
                    events.append((current["concept:name"], seconds(current.get("time:timestamp"))))
                element.clear()
            elif tag == "trace":
                if events:
                    yield sorted(events, key=lambda item: (item[1] is None, item[1] or 0.0))
                element.clear()
        if not seen_log:
            raise LogError("the XML file is not an XES log")
    except ElementTree.ParseError as exc:
        raise LogError("the XES file could not be read") from exc
    finally:
        stream.close()


def _ocel(path: Path) -> Iterator[Case]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LogError("the JSON file could not be read") from exc
    if not isinstance(data, dict) or "events" not in data or "objects" not in data:
        raise LogError("the JSON file is not an OCEL 2.0 log (it needs events and objects)")
    types = {item["id"]: item.get("type") for item in data["objects"] if isinstance(item, dict) and "id" in item}
    per_type: dict[str, int] = defaultdict(int)
    for event in data["events"]:
        for link in event.get("relationships") or []:
            per_type[types.get(link.get("objectId"))] += 1
    if not per_type:
        raise LogError("the OCEL log links no events to objects")
    # The object type most events touch is the case notion, such as the application in a loan log.
    notion = max((name for name in per_type if name), key=lambda name: per_type[name])
    cases: dict[str, Case] = defaultdict(list)
    for event in data["events"]:
        for link in event.get("relationships") or []:
            if types.get(link.get("objectId")) == notion:
                cases[link["objectId"]].append((event.get("type", ""), seconds(event.get("time"))))
    return _sorted(cases)


def peek_csv_header(raw: bytes) -> list[str]:
    """The header of a CSV file, to recognize exports such as the CFPB complaint database."""
    text = raw[:65536].decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return next(csv.reader(io.StringIO(text), dialect), [])
