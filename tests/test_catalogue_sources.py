"""Hotel and airline catalogue sources: their adapters, the calibration they give, and a calibrated hotel run."""

import csv
import io
import zipfile
from datetime import date, timedelta

import pytest

from app import runtime
from app.calibrate import _hotel_bookings, _on_time
from app.eventlog import LogError
from sectors.calibration import Calibration
from test_api import _auth
from test_calibration import CatalogueFetcher

HOTEL_HEADER = [
    "hotel", "is_canceled", "lead_time", "arrival_date_year", "arrival_date_month", "arrival_date_week_number",
    "arrival_date_day_of_month", "stays_in_weekend_nights", "stays_in_week_nights", "adults", "booking_changes",
    "deposit_type", "customer_type", "adr", "reservation_status", "reservation_status_date",
]


def hotel_csv(count: int = 200, header=HOTEL_HEADER) -> bytes:
    """Bookings in the published file's columns: 40% cancelled (a quarter with a kept deposit), 5% no-shows, the rest
    checked out; every fifth booking was changed."""
    rows = []
    for number in range(count):
        arrival = date(2016, 1 + number % 12, 1 + number % 28)
        lead = 10 + (number * 7) % 200
        nights = 1 + number % 5
        if number % 20 < 8:
            status, closed, deposit = "Canceled", arrival - timedelta(days=max(1, lead // 2)), "Non Refund" if number % 4 == 0 else "No Deposit"
        elif number % 20 == 8:
            status, closed, deposit = "No-Show", arrival, "No Deposit"
        else:
            status, closed, deposit = "Check-Out", arrival + timedelta(days=nights), "No Deposit"
        rows.append([
            "City Hotel", int(status == "Canceled"), lead, arrival.year, arrival.strftime("%B"), 1, arrival.day, nights // 2, nights - nights // 2,
            2, int(number % 5 == 0), deposit, "Transient", 99.5, status, closed.isoformat(),
        ])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode()


BTS_HEADER = ["Year", "Month", "FlightDate", "Reporting_Airline", "Origin", "Dest", "DepDelay", "DepDel15", "ArrDelay", "ArrDel15", "Cancelled", "Diverted", "ActualElapsedTime"]


def bts_zip(header=BTS_HEADER) -> bytes:
    """A month of flights as the BTS archive packs it: 70 on time, 20 departing an hour late (15 of them arriving
    late), 5 arriving late after an on-time departure, 3 cancelled, and 2 diverted: 25 delayed, 20 arriving late."""
    rows = []
    rows += [[2026, 7, "2026-07-01", "AA", "JFK", "LAX", -3, 0, -5, 0, 0, 0, 360]] * 70
    rows += [[2026, 7, "2026-07-02", "DL", "ATL", "ORD", 60, 1, 55, 1, 0, 0, 120]] * 15
    rows += [[2026, 7, "2026-07-02", "DL", "ATL", "ORD", 60, 1, 10, 0, 0, 0, 100]] * 5
    rows += [[2026, 7, "2026-07-03", "UA", "SFO", "DEN", 5, 0, 20, 1, 0, 0, 150]] * 5
    rows += [[2026, 7, "2026-07-04", "WN", "DAL", "HOU", "", "", "", "", 1, 0, ""]] * 3
    rows += [[2026, 7, "2026-07-05", "B6", "BOS", "MCO", 10, 0, "", "", 0, 1, ""]] * 2
    text = io.StringIO()
    writer = csv.writer(text, quoting=csv.QUOTE_NONNUMERIC)
    writer.writerow(header)
    writer.writerows(rows)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("On_Time_Reporting_Carrier_On_Time_Performance_(1987_present)_2026_7.csv", text.getvalue())
        out.writestr("readme.html", "<html>Field descriptions</html>")
    return archive.getvalue()


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_the_catalogue_lists_hotel_and_airline_sources_with_their_licences(client):
    hotel = {entry["id"]: entry for entry in client.get("/catalogue", params={"sector": "hotel"}).json()["data"]}
    assert hotel["hotel_booking_demand"]["licence"] == "CC BY 4.0" and hotel["hotel_booking_demand"]["doi"] == "10.1016/j.dib.2018.11.126"
    assert hotel["hotel_booking_demand"]["bytes"] == 16_855_599 and hotel["hotel_booking_demand"]["availability"] == "download"
    airline = {entry["id"]: entry for entry in client.get("/catalogue", params={"sector": "airline"}).json()["data"]}
    assert airline["bts_on_time"]["licence"].startswith("Public domain") and airline["bts_on_time"]["bytes"] == 33_075_361
    assert airline["bts_on_time"]["url"].endswith("_2026_7.zip")
    for sector in ("telecom", "insurance"):
        listed = client.get("/catalogue", params={"sector": sector}).json()["data"]
        assert [entry["availability"] for entry in listed] == ["planned"] and "terms" in listed[0]["reason"]


# ---------------------------------------------------------------------------
# Hotel booking demand
# ---------------------------------------------------------------------------


def test_bookings_become_reservation_paths_timed_from_the_booking(tmp_path):
    path = tmp_path / "hotels.csv"
    path.write_bytes(hotel_csv())
    result = _hotel_bookings(path)
    assert result["format"] == "Hotel booking demand" and result["cases"] == 200 and result["skipped"] == 0
    assert result["outcomes"] == {"cancelled": 0.4, "no_show": 0.05, "arrived": 0.55, "changed": 0.2}
    calibration = Calibration.from_dict(result["calibration"])
    confirmed = calibration.transitions["reservation.confirmed"]
    assert set(confirmed) == {"reservation.cancelled", "room.assigned", "modification.requested"}
    assert calibration.transitions["room.assigned"] == {"guest.checked_in": 110, "guest.no_show": 10}
    assert calibration.transitions["reservation.cancelled"]["cancellation.fee_charged"] == 20
    # The data holds only bookings that were made: it says nothing about guarantees, so they keep the pack's weights.
    assert not {"guarantee.accepted", "guarantee.declined", "reservation.lapsed", "reservation.created"} & calibration.observed_events
    options = [("guarantee.accepted", 0.9), ("guarantee.declined", 0.05), ("reservation.lapsed", 0.05)]
    assert calibration.reweight("reservation.created", options) == options
    # Lead time, time to cancellation, and length of stay are timed.
    median, low, high, samples = calibration.dwell["reservation.confirmed>room.assigned"]
    assert samples > 50 and 10 * 24 <= low <= median <= high <= 210 * 24
    assert calibration.dwell["guest.checked_in>guest.checked_out"][3] == 110
    assert calibration.dwell["reservation.confirmed>reservation.cancelled"][3] > 0


def test_the_original_datasets_column_names_are_read_too(tmp_path):
    header = ["Hotel", "IsCanceled", "LeadTime", "ArrivalDateYear", "ArrivalDateMonth", "ArrivalDateWeekNumber", "ArrivalDateDayOfMonth",
              "StaysInWeekendNights", "StaysInWeekNights", "Adults", "BookingChanges", "DepositType", "CustomerType", "ADR",
              "ReservationStatus", "ReservationStatusDate"]
    path = tmp_path / "H2.csv"
    path.write_bytes(hotel_csv(40, header=header))
    assert _hotel_bookings(path)["cases"] == 40
    broken = tmp_path / "other.csv"
    broken.write_text("hotel,adr\nCity Hotel,90\n")
    with pytest.raises(LogError, match="no arrivaldatedayofmonth"):
        _hotel_bookings(broken)


def _hotel_study(client, email):
    headers = _auth(client, email, "password-123")
    project = client.post("/projects", headers=headers, json={"name": "Lisbon stays", "sector": "hotel"}).json()
    response = client.post(f"/projects/{project['id']}/corpus", headers=headers, data={"kind": "paper"},
                           files={"upload": ("notes.md", b"Guests book, some cancel, and most arrive.", "application/octet-stream")})
    assert response.status_code == 200, response.text
    return headers, project["id"]


def _hotel_run(client, headers, project_id, **overrides):
    body = {
        "project_id": project_id, "sector": "hotel", "target_trajectory_count": 64, "event_budget": None, "min_events": 4, "max_events": 16,
        "max_assistant_turns": 4, "sub_domains": ["booking_and_reservations", "modifications_and_cancellations", "arrival_and_check_in"],
        "language": "en", "start_mode": "warm", "cold_start_acknowledged": False, "reward_mechanism": "binary_outcome",
        "signal_mechanism": "outcome", "consumer": "post_training", "target_family": "llm", "max_cycles": 1,
    }
    response = client.post("/runs", headers=headers, json={**body, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def _cancelled_share(run) -> float:
    kinds = {event["event_id"]: event["event_type"] for event in run["bundle"]["events"]}
    primaries = [[kinds[item] for item in trajectory["event_ids"]] for trajectory in run["bundle"]["trajectories"] if not trajectory.get("parent_trajectory_id")]
    confirmed = [types for types in primaries if "reservation.confirmed" in types]
    return sum("reservation.cancelled" in types for types in confirmed) / len(confirmed)


def test_a_hotel_run_calibrated_from_the_catalogue_reports_representativeness(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _hotel_study(client, "hotel-catalogue@example.com")
    runtime.fetcher = CatalogueFetcher(hotel_csv(400))
    added = client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "hotel_booking_demand"}).json()
    assert runtime.fetcher.urls == ["https://raw.githubusercontent.com/rfordatascience/tidytuesday/master/data/2020/2020-02-11/hotels.csv"]
    assert added["ingest"]["licence"] == "CC BY 4.0" and added["ingest"]["citation"].startswith("Antonio, N.")
    project = next(item for item in client.get("/projects", headers=headers).json()["data"] if item["id"] == project_id)
    source = next(item for item in project["corpus"] if item["kind"] == "data_source")
    assert source["calibration"]["status"] == "ready" and source["calibration"]["format"] == "Hotel booking demand"

    plain = _hotel_run(client, headers, project_id, calibrate=False)
    calibrated = _hotel_run(client, headers, project_id)
    assert calibrated["generation"]["calibration"]["cases"] == 400
    representative = calibrated["generation"]["quality"]["representative"]
    assert representative["status"] == "measured" and representative["sources"] == ["Hotel booking demand"]
    assert 0 < representative["fitness"] <= 1 and 0 < representative["precision"] <= 1
    # The data cancels two confirmed reservations in five; the pack's prior cancels far fewer.
    assert _cancelled_share(calibrated) > _cancelled_share(plain) + 0.1


# ---------------------------------------------------------------------------
# BTS on-time performance
# ---------------------------------------------------------------------------


def test_flights_become_checked_in_passengers_paths_to_arrival(tmp_path):
    path = tmp_path / "ontime.zip"
    path.write_bytes(bts_zip())
    result = _on_time(path)
    assert result["cases"] == 98 and result["activities"]["diverted, left out"] == 2
    assert result["outcomes"] == {"delayed": pytest.approx(25 / 98, abs=1e-4), "cancelled": pytest.approx(3 / 98, abs=1e-4), "arrived_late_when_delayed": 0.8}
    calibration = Calibration.from_dict(result["calibration"])
    assert calibration.transitions["passenger.checked_in"] == {"passenger.boarded": 70, "flight.delayed": 25, "flight.cancelled": 3}
    assert calibration.transitions["flight.departed"] == {"flight.arrived": 75, "flight.arrived_late": 20}
    # A delay runs from the scheduled departure to the actual one; flights take their elapsed time.
    assert calibration.dwell["flight.delayed>passenger.boarded"][0] == 1.0
    assert calibration.dwell["flight.departed>flight.arrived"][3] == 75
    # Boarding is not timed apart from departure, so the pack keeps its own gap between them.
    assert calibration.dwell["passenger.boarded>flight.departed"][0] == 0.0
    # Checked bags are not in flight records: dropping one after check-in keeps the pack's weight.
    options = [("bag.dropped", 1.0), ("passenger.boarded", 0.91), ("flight.delayed", 0.12), ("flight.cancelled", 0.03)]
    blended = dict(calibration.reweight("passenger.checked_in", options))
    assert blended["bag.dropped"] == 1.0 and blended["flight.delayed"] > 0.12


def test_the_transtats_download_column_names_are_read_too(tmp_path):
    header = ["YEAR", "MONTH", "FL_DATE", "OP_UNIQUE_CARRIER", "ORIGIN", "DEST", "DEP_DELAY", "DEP_DEL15", "ARR_DELAY", "ARR_DEL15", "CANCELLED", "DIVERTED", "ACTUAL_ELAPSED_TIME"]
    archive = zipfile.ZipFile(io.BytesIO(bts_zip(header)))
    csv_path = tmp_path / "T_ONTIME_REPORTING.csv"
    csv_path.write_bytes(archive.read(archive.namelist()[0]))
    assert _on_time(csv_path)["cases"] == 98


def test_an_uploaded_flight_archive_is_recognized_and_calibrates_an_airline_study(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "airline-upload@example.com", "password-123")
    project = client.post("/projects", headers=headers, json={"name": "Summer flying", "sector": "airline"}).json()
    response = client.post(f"/projects/{project['id']}/corpus", headers=headers, data={"kind": "data_source"},
                           files={"upload": ("On_Time_2026_7.zip", bts_zip(), "application/zip")})
    assert response.status_code == 200, response.text
    assert response.json()["calibration"]["format"] == "BTS on-time performance"
    runtime.fetcher = CatalogueFetcher(bts_zip())
    added = client.post(f"/projects/{project['id']}/catalogue", headers=headers, json={"entry": "bts_on_time"}).json()
    assert added["ingest"]["licence"].startswith("Public domain")
    corpus = next(item for item in client.get("/projects", headers=headers).json()["data"] if item["id"] == project["id"])["corpus"]
    fetched = next(item for item in corpus if item["id"] == added["id"])
    assert fetched["calibration"]["format"] == "BTS on-time performance" and fetched["calibration"]["cases"] == 98
