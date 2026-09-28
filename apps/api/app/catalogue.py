"""Public data sources a study can calibrate from, downloaded on demand into the upload store.

Nothing here is committed to the repository. Each source records its licence, where it came from, and
when it was fetched. Sources whose terms are still under review, or whose data serves later slices, are
listed with the reason they cannot be added yet.
"""

from __future__ import annotations

# BPI Challenge 2017 activities mapped to banking events. Offers and most work items are left unmapped:
# they have no event of their own in the pack, and an activity left out here stays unmapped unless a person
# maps it. A_Pending is the state of a granted loan.
BPI2017_MAPPING = {
    "A_Create Application": "application.started",
    "A_Submitted": "application.submitted",
    "W_Validate application": "kyc.started",
    # The bank's decision follows validation, as the pack's decision follows a passed check.
    "A_Validating": "kyc.passed",
    "A_Incomplete": "kyc.review_required",
    "W_Assess potential fraud": "kyc.review_required",
    "A_Pending": "application.approved",
    "A_Denied": "application.declined",
    "A_Cancelled": "application.abandoned",
}

ENTRIES = [
    {
        "id": "bpi2017",
        "name": "BPI Challenge 2017",
        "sector": "banking",
        "sub_domains": ["onboarding_and_kyc", "consumer_credit"],
        "availability": "download",
        "url": "https://data.4tu.nl/file/34c3f44b-3101-4ea9-8281-e38905c68b8d/f3aec4f7-d52c-4217-82f4-57d719a8298c",
        "file": "BPI Challenge 2017.xes.gz",
        "bytes": 29_658_747,
        "licence": "4TU.ResearchData General Terms of Use",
        "doi": "10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b",
        "citation": "van Dongen, B. (2017). BPI Challenge 2017. 4TU.ResearchData.",
        "describes": "Loan applications at a Dutch financial institute from 2016 to February 2017: about 31,500 applications and 1.2 million events.",
        "calibrates": "Next-step shares and durations from application to approval, decline, or cancellation.",
        "mapping": BPI2017_MAPPING,
    },
    {
        "id": "uci_bank_marketing",
        "name": "UCI Bank Marketing",
        "sector": "banking",
        "sub_domains": ["deposits"],
        "availability": "download",
        "url": "https://archive.ics.uci.edu/static/public/222/bank+marketing.zip",
        "file": "bank+marketing.zip",
        "bytes": 1_000_000,
        "licence": "CC BY 4.0",
        "doi": "10.24432/C5K306",
        "citation": "Moro, S., Rita, P., & Cortez, P. (2014). Bank Marketing. UCI Machine Learning Repository.",
        "describes": "Phone campaigns of a Portuguese bank offering term deposits, 2008 to 2010: 41,188 contacts.",
        "calibrates": "The channel of deposit offers and how often an offer turns into an application.",
    },
    {
        "id": "cfpb_complaints",
        "name": "CFPB Consumer Complaint Database",
        "sector": "banking",
        "sub_domains": ["complaints"],
        "availability": "upload",
        "url": "https://www.consumerfinance.gov/data-research/consumer-complaints/",
        "licence": "CC0 1.0 (public domain)",
        "citation": "Consumer Financial Protection Bureau, Consumer Complaint Database.",
        "describes": "Complaints about US financial products. Export a CSV filtered to the products you study and upload it as a data source; its columns are recognized.",
        "calibrates": "The channels complaints arrive through and how often they end in relief.",
        "reason": "The complaint search API no longer answers and the full file is over a gigabyte, so this source is added from an export.",
    },
    {
        "id": "hotel_booking_demand",
        "name": "Hotel booking demand",
        "sector": "hotel",
        "sub_domains": ["booking_and_reservations", "modifications_and_cancellations", "arrival_and_check_in", "check_out_and_billing"],
        "availability": "download",
        # The article's H1 (resort) and H2 (city) files, combined as published by the TidyTuesday project.
        "url": "https://raw.githubusercontent.com/rfordatascience/tidytuesday/master/data/2020/2020-02-11/hotels.csv",
        "file": "hotels.csv",
        "bytes": 16_855_599,
        "licence": "CC BY 4.0",
        "doi": "10.1016/j.dib.2018.11.126",
        "citation": "Antonio, N., de Almeida, A., & Nunes, L. (2019). Hotel booking demand datasets. Data in Brief, 22, 41-49.",
        "describes": "Bookings at a resort hotel in the Algarve and a city hotel in Lisbon with arrivals from July 2015 to August 2017: 119,390 bookings with lead time, changes, deposit type, and final status. The file combines the article's two datasets.",
        "calibrates": "How often a confirmed reservation is changed, cancelled, or arrives; whether a cancelled deposit is kept or refunded; whether a guest arrives or does not show; the lead time from booking to arrival, the time to cancellation, and the length of stay.",
    },
    {
        "id": "bts_on_time",
        "name": "BTS on-time performance, July 2026",
        "sector": "airline",
        "sub_domains": ["check_in_and_boarding", "disruption_and_compensation"],
        "availability": "download",
        "url": "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_7.zip",
        "file": "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_7.zip",
        "bytes": 33_075_361,
        "licence": "Public domain (US federal government data)",
        "citation": "Bureau of Transportation Statistics, Reporting Carrier On-Time Performance (1987-present), July 2026.",
        "describes": "Every domestic flight of the large US carriers in July 2026, with departure and arrival delays, cancellations, diversions, and elapsed times.",
        "calibrates": "How often a flight is delayed or cancelled once passengers have checked in, how long a delay lasts, how often a delayed flight arrives late, and how long flights take.",
    },
    {"id": "hmda", "name": "HMDA", "sector": "banking", "availability": "planned", "reason": "Follows once its terms are reviewed."},
    {"id": "freddie_mac", "name": "Freddie Mac single-family loan-level data", "sector": "banking", "availability": "planned", "reason": "Follows once its terms are reviewed; it needs registration."},
    {"id": "amlsim", "name": "AMLSim", "sector": "banking", "availability": "planned", "reason": "Transaction patterns arrive with the episode builder in Slice 6."},
    {"id": "paysim", "name": "PaySim", "sector": "banking", "availability": "planned", "reason": "Transaction patterns arrive with the episode builder in Slice 6."},
    # Reviewed on 28 September 2026 (decision 16); neither is added, and each says why.
    {
        "id": "fcc_complaints",
        "name": "FCC consumer complaints (CGB)",
        "sector": "telecom",
        "availability": "reviewed",
        "url": "https://opendata.fcc.gov/d/3xyp-aqkj",
        "licence": "Public domain (US government work); the FCC API terms ask for a notice that the FCC does not endorse the product",
        "reason": (
            "Its terms allow it, but each complaint records only when it was filed and about what: no channel of filing, "
            "provider, outcome, or closing date. It cannot calibrate any step after a complaint is received."
        ),
    },
    {
        "id": "tdi_complaints",
        "name": "Texas Department of Insurance complaints",
        "sector": "insurance",
        "availability": "reviewed",
        "url": "https://data.texas.gov/d/ubdr-4uff",
        "licence": "None stated; TDI's site terms allow copies only unaltered and for non-commercial use",
        "reason": (
            "Its received and closed dates, confirmation, and dispositions would calibrate complaint outcomes and their "
            "timing, but it waits on TDI's written confirmation that aggregates derived from it may be published."
        ),
    },
]

BY_ID = {entry["id"]: entry for entry in ENTRIES}
# The largest file a catalogue download may be; BPI Challenge 2017 and a month of BTS flights are about 30 MB.
MAX_DOWNLOAD = 60_000_000


def public(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "mapping"}
