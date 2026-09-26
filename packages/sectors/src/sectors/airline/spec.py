"""Airline passenger journeys as orthogonal state machines, in the vocabulary of IATA's NDC and ONE Order.

Dimensions: relationship and loyalty on the party; the order with its payment, seat, change, check-in,
boarding, eligibility for compensation, and miles; the flight's status and delay; the checked bag; the
compensation claim; and the complaint case. A disrupted order, after a cancelled flight or denied
boarding, is rebooked or refunded. Weights are hand-set priors until data sources calibrate them.
"""

from __future__ import annotations

from typing import Any

from sectors.journeys import Amount, PackSpec
from sectors.lifecycle import EventSpec, LifecycleSpec, need, put

GENERATOR_ID = "airline-semi-markov-v1"
PACK_VERSION = "airline-pack-1"

SB = "shopping_and_booking"
AC = "ancillaries_and_changes"
CB = "check_in_and_boarding"
DC = "disruption_and_compensation"
BG = "baggage"
LY = "loyalty"
CO = "complaints"

DAY = 24.0
BEFORE_DEPARTURE = ("scheduled", "delayed")
NOT_BOARDED = (None, "pending")

LIFECYCLE = LifecycleSpec(
    object_types={
        "party": "party",
        "offering": "fare_offer",
        "order": "order",
        "flight": "flight",
        "bag": "bag",
        "claim": "compensation_claim",
        "complaint": "complaint",
    },
    events=(
        EventSpec(
            "offer.viewed", (SB,),
            requires=(need("party", "relationship", None),),
            sets=(put("party", "relationship", "prospect"),),
            dwell_hours=(0.0, 0.0), opening=True,
        ),
        EventSpec(
            "order.created", (SB,),
            requires=(need("party", "relationship", "prospect"), need("order", "order", None)),
            sets=(put("order", "order", "created"), put("flight", "status", "scheduled"), put("flight", "delay", "no")),
            dwell_hours=(0.05, 1.0),
            violation="order created before an offer was viewed",
        ),
        EventSpec(
            "payment.captured", (SB,),
            requires=(need("order", "order", "created"), need("order", "payment", None)),
            sets=(put("order", "payment", "captured"),),
            weight=0.88, outcome="payment_outcome", dwell_hours=(0.01, 0.5),
            violation="payment taken outside an unpaid order",
        ),
        EventSpec(
            "payment.failed", (SB,),
            requires=(need("order", "order", "created"), need("order", "payment", None)),
            sets=(put("order", "payment", "failed"),),
            weight=0.05, outcome="payment_outcome", ends_journey=True, dwell_hours=(0.01, 0.5),
            violation="payment failed outside an unpaid order",
        ),
        EventSpec(
            "order.expired", (SB,),
            requires=(need("order", "order", "created"), need("order", "payment", None)),
            sets=(put("order", "order", "expired"),),
            weight=0.07, outcome="payment_outcome", ends_journey=True, dwell_hours=(DAY, 3 * DAY),
            violation="order expired after it was paid",
        ),
        EventSpec(
            "order.confirmed", (SB,),
            requires=(need("order", "order", "created"), need("order", "payment", "captured")),
            sets=(put("order", "order", "confirmed"), put("party", "relationship", "customer")),
            dwell_hours=(0.01, 2.0),
            violation="order ticketed before it was paid",
        ),
        EventSpec(
            "seat.selected", (AC,),
            requires=(need("order", "order", "confirmed"), need("order", "seat", None), need("order", "boarding", *NOT_BOARDED)),
            sets=(put("order", "seat", "selected"),),
            weight=0.45, dwell_hours=(0.1, 10 * DAY),
            violation="seat chosen before the order was ticketed or after boarding",
        ),
        EventSpec(
            "bag.added", (AC, BG),
            requires=(need("order", "order", "confirmed"), need("bag", "bag", None), need("order", "boarding", *NOT_BOARDED)),
            sets=(put("bag", "bag", "added"),),
            weight=0.45, dwell_hours=(0.1, 20 * DAY),
            violation="bag added before the order was ticketed or after boarding",
        ),
        EventSpec(
            "change.requested", (AC,),
            requires=(
                need("order", "order", "confirmed"), need("flight", "status", *BEFORE_DEPARTURE),
                need("order", "boarding", *NOT_BOARDED), need("order", "change", None, "changed", "declined"),
            ),
            sets=(put("order", "change", "requested"),),
            weight=0.12, repeat=2, dwell_hours=(DAY, 30 * DAY),
            violation="change requested after departure or before ticketing",
        ),
        EventSpec(
            "order.changed", (AC,),
            requires=(need("order", "change", "requested"),),
            sets=(put("order", "change", "changed"),),
            weight=0.8, repeat=2, outcome="change_decision", dwell_hours=(0.1, 24.0),
            violation="order changed without a request",
        ),
        EventSpec(
            "change.declined", (AC,),
            requires=(need("order", "change", "requested"),),
            sets=(put("order", "change", "declined"),),
            weight=0.2, repeat=2, outcome="change_decision", dwell_hours=(0.1, 24.0),
            violation="change declined without a request",
        ),
        EventSpec(
            "order.cancelled", (AC,),
            requires=(need("order", "order", "confirmed"), need("flight", "status", *BEFORE_DEPARTURE), need("order", "boarding", *NOT_BOARDED)),
            sets=(put("order", "order", "cancelled"),),
            weight=0.04, dwell_hours=(DAY, 30 * DAY),
            violation="order cancelled after departure or before ticketing",
        ),
        EventSpec(
            "refund.issued", (AC, DC),
            requires=(need("order", "order", "cancelled", "disrupted"),),
            sets=(put("order", "order", "refunded"),),
            weight=0.25, outcome="disruption_choice", dwell_hours=(DAY, 14 * DAY),
            violation="refund issued for an order that was neither cancelled nor disrupted",
        ),
        EventSpec(
            "loyalty.enrolled", (LY,),
            requires=(need("party", "relationship", "prospect", "customer"), need("party", "loyalty", None)),
            sets=(put("party", "loyalty", "member"),),
            weight=0.2, dwell_hours=(0.1, 30 * DAY),
            violation="loyalty enrolment twice",
        ),
        EventSpec(
            "passenger.checked_in", (CB,),
            requires=(need("order", "order", "confirmed"), need("flight", "status", *BEFORE_DEPARTURE), need("order", "checkin", None, "pending")),
            sets=(put("order", "checkin", "checked_in"),),
            repeat=2, dwell_hours=(DAY, 40 * DAY),
            violation="check-in before ticketing or after the flight left",
        ),
        EventSpec(
            "bag.dropped", (BG,),
            requires=(need("bag", "bag", "added"), need("order", "checkin", "checked_in"), need("order", "boarding", *NOT_BOARDED)),
            sets=(put("bag", "bag", "dropped"),),
            dwell_hours=(0.5, 3.0),
            violation="bag dropped before check-in or after boarding",
        ),
        EventSpec(
            "passenger.boarded", (CB,),
            # A checked bag goes in the hold before its passenger boards.
            requires=(
                need("order", "checkin", "checked_in"), need("flight", "status", *BEFORE_DEPARTURE),
                need("order", "boarding", *NOT_BOARDED), need("bag", "bag", None, "dropped"),
            ),
            sets=(put("order", "boarding", "boarded"),),
            weight=0.955, outcome="boarding_outcome", dwell_hours=(0.5, 4.0),
            violation="boarding before check-in, before a checked bag was dropped, or after the flight left",
        ),
        EventSpec(
            "passenger.no_show", (CB,),
            requires=(need("order", "checkin", "checked_in"), need("flight", "status", *BEFORE_DEPARTURE), need("order", "boarding", *NOT_BOARDED)),
            sets=(put("order", "boarding", "no_show"),),
            weight=0.03, outcome="boarding_outcome", ends_journey=True, dwell_hours=(0.5, 4.0),
            violation="no-show recorded before check-in",
        ),
        EventSpec(
            "boarding.denied", (CB,),
            requires=(
                need("order", "checkin", "checked_in"), need("flight", "status", *BEFORE_DEPARTURE),
                need("order", "boarding", *NOT_BOARDED), need("bag", "bag", None, "dropped"),
            ),
            sets=(put("order", "boarding", "denied"), put("order", "order", "disrupted"), put("order", "eligible", "yes")),
            # Rare in practice; kept visible so the rebooking and compensation paths are drawn.
            weight=0.015, outcome="boarding_outcome", dwell_hours=(0.5, 4.0),
            violation="boarding denied before check-in",
        ),
        EventSpec(
            "flight.delayed", (DC,),
            requires=(need("order", "order", "confirmed"), need("flight", "status", "scheduled")),
            sets=(put("flight", "status", "delayed"), put("flight", "delay", "yes")),
            weight=0.12, repeat=2, dwell_hours=(0.5, 48.0),
            violation="flight delayed before booking or after departure",
        ),
        EventSpec(
            "flight.cancelled", (DC,),
            requires=(need("order", "order", "confirmed"), need("flight", "status", *BEFORE_DEPARTURE), need("order", "boarding", *NOT_BOARDED)),
            sets=(put("flight", "status", "cancelled"), put("order", "order", "disrupted"), put("order", "eligible", "yes")),
            weight=0.03, dwell_hours=(1.0, 14 * DAY),
            violation="flight cancelled after the passenger boarded or before booking",
        ),
        EventSpec(
            "order.rebooked", (DC,),
            # A new flight: check-in and boarding start again, and the old delay no longer counts.
            requires=(need("order", "order", "disrupted"),),
            sets=(
                put("order", "order", "confirmed"), put("flight", "status", "scheduled"), put("flight", "delay", "no"),
                put("order", "checkin", "pending"), put("order", "boarding", "pending"),
            ),
            weight=0.75, repeat=2, outcome="disruption_choice", dwell_hours=(0.5, 24.0),
            violation="rebooking without a disruption",
        ),
        EventSpec(
            "flight.departed", (CB,),
            requires=(need("flight", "status", *BEFORE_DEPARTURE), need("order", "boarding", "boarded")),
            sets=(put("flight", "status", "departed"),),
            dwell_hours=(0.2, 2.0),
            violation="flight departed before the passenger boarded",
        ),
        EventSpec(
            "flight.arrived", (CB,),
            requires=(need("flight", "status", "departed"),),
            sets=(put("flight", "status", "arrived"),),
            weight=0.8, outcome="arrival", dwell_hours=(1.0, 14.0),
            violation="flight arrived before it departed",
        ),
        EventSpec(
            "flight.arrived_late", (DC,),
            requires=(need("flight", "status", "departed"), need("flight", "delay", "yes")),
            sets=(put("flight", "status", "arrived"), put("order", "eligible", "yes")),
            weight=0.2, outcome="arrival", dwell_hours=(4.0, 18.0),
            violation="late arrival recorded for a flight that was never delayed",
        ),
        EventSpec(
            "bag.delivered", (BG,),
            requires=(need("bag", "bag", "dropped"), need("flight", "status", "arrived")),
            sets=(put("bag", "bag", "delivered"),),
            weight=0.95, outcome="baggage_outcome", dwell_hours=(0.2, 1.0),
            violation="bag delivered before the flight arrived",
        ),
        EventSpec(
            "bag.delayed", (BG,),
            requires=(need("bag", "bag", "dropped"), need("flight", "status", "arrived")),
            sets=(put("bag", "bag", "delayed"),),
            weight=0.05, outcome="baggage_outcome", dwell_hours=(0.2, 1.0),
            violation="bag reported delayed before the flight arrived",
        ),
        EventSpec(
            "bag.returned", (BG,),
            requires=(need("bag", "bag", "delayed"),),
            sets=(put("bag", "bag", "returned"),),
            dwell_hours=(DAY, 5 * DAY),
            violation="bag returned without being delayed",
        ),
        EventSpec(
            "miles.credited", (LY,),
            requires=(need("party", "loyalty", "member"), need("flight", "status", "arrived"), need("order", "miles", None)),
            sets=(put("order", "miles", "credited"),),
            dwell_hours=(DAY, 14 * DAY),
            violation="miles credited before the flight arrived or to a non-member",
        ),
        EventSpec(
            "compensation.claimed", (DC,),
            requires=(need("order", "eligible", "yes"), need("claim", "claim", None)),
            sets=(put("claim", "claim", "open"),),
            weight=0.5, dwell_hours=(DAY, 60 * DAY),
            violation="compensation claimed without a delay, cancellation, or denied boarding",
        ),
        EventSpec(
            "compensation.paid", (DC,),
            requires=(need("claim", "claim", "open"),),
            sets=(put("claim", "claim", "paid"),),
            weight=0.65, outcome="compensation_decision", dwell_hours=(7 * DAY, 60 * DAY),
            violation="compensation paid without a claim",
        ),
        EventSpec(
            "compensation.rejected", (DC,),
            requires=(need("claim", "claim", "open"),),
            sets=(put("claim", "claim", "rejected"),),
            weight=0.35, outcome="compensation_decision", dwell_hours=(7 * DAY, 60 * DAY),
            violation="compensation rejected without a claim",
        ),
        EventSpec(
            "complaint.received", (CO,),
            requires=(need("party", "relationship", "customer"), need("complaint", "complaint", None)),
            sets=(put("complaint", "complaint", "open"),),
            weight=0.05, dwell_hours=(DAY, 30 * DAY),
            violation="complaint received twice or before a booking was ticketed",
        ),
        EventSpec(
            "complaint.resolved", (CO,),
            requires=(need("complaint", "complaint", "open"),),
            sets=(put("complaint", "complaint", "resolved"),),
            weight=0.75, outcome="complaint_outcome", dwell_hours=(DAY, 56 * DAY),
            violation="complaint resolved before it was received",
        ),
        EventSpec(
            "complaint.escalated", (CO,),
            requires=(need("complaint", "complaint", "open"),),
            sets=(put("complaint", "complaint", "escalated"),),
            weight=0.25, outcome="complaint_outcome", dwell_hours=(56 * DAY, 90 * DAY),
            violation="complaint escalated before it was received",
        ),
    ),
    milestones={
        SB: ("order.confirmed", "payment.failed", "order.expired"),
        AC: ("seat.selected", "bag.added", "order.changed", "change.declined", "refund.issued"),
        CB: ("passenger.boarded", "passenger.no_show", "boarding.denied"),
        DC: ("flight.cancelled", "flight.arrived_late", "compensation.paid", "compensation.rejected"),
        BG: ("bag.delivered", "bag.delayed"),
        LY: ("loyalty.enrolled", "miles.credited"),
        CO: ("complaint.received",),
    },
)

OBJECTS = {
    "party": ("P", "party", "individual"),
    "offering": ("F", "fare_offer", "economy"),
    "order": ("O", "order", "economy"),
    "flight": ("L", "flight", "short_haul"),
    "bag": ("G", "bag", "checked"),
    "claim": ("Q", "compensation_claim", "passenger_rights"),
    "complaint": ("M", "complaint", "service"),
}

PASSENGER = ("party", "passenger")
ROLES = {
    "offer.viewed": (("party", "prospect"), ("offering", "offer")),
    "order.created": (PASSENGER, ("order", "order"), ("flight", "flight")),
    "payment.captured": (PASSENGER, ("order", "order")),
    "payment.failed": (PASSENGER, ("order", "order")),
    "order.expired": (PASSENGER, ("order", "order")),
    "order.confirmed": (PASSENGER, ("order", "order")),
    "seat.selected": (PASSENGER, ("order", "order"), ("flight", "flight")),
    "bag.added": (PASSENGER, ("order", "order"), ("bag", "bag")),
    "change.requested": (PASSENGER, ("order", "order")),
    "order.changed": (PASSENGER, ("order", "order")),
    "change.declined": (PASSENGER, ("order", "order")),
    "order.cancelled": (PASSENGER, ("order", "order")),
    "refund.issued": (PASSENGER, ("order", "order")),
    "loyalty.enrolled": (("party", "member"),),
    "passenger.checked_in": (PASSENGER, ("order", "order"), ("flight", "flight")),
    "bag.dropped": (PASSENGER, ("bag", "bag"), ("flight", "flight")),
    "passenger.boarded": (PASSENGER, ("order", "order"), ("flight", "flight")),
    "passenger.no_show": (PASSENGER, ("order", "order"), ("flight", "flight")),
    "boarding.denied": (PASSENGER, ("order", "order"), ("flight", "flight")),
    "flight.delayed": (("flight", "flight"), ("order", "order")),
    "flight.cancelled": (("flight", "flight"), ("order", "order")),
    "order.rebooked": (PASSENGER, ("order", "order"), ("flight", "flight")),
    "flight.departed": (("flight", "flight"),),
    "flight.arrived": (("flight", "flight"),),
    "flight.arrived_late": (("flight", "flight"), ("order", "order")),
    "bag.delivered": (("bag", "bag"), ("flight", "flight")),
    "bag.delayed": (("bag", "bag"), ("flight", "flight")),
    "bag.returned": (PASSENGER, ("bag", "bag")),
    "miles.credited": (("party", "member"), ("order", "order")),
    "compensation.claimed": (("party", "claimant"), ("claim", "claim"), ("order", "order")),
    "compensation.paid": (("party", "claimant"), ("claim", "claim")),
    "compensation.rejected": (("party", "claimant"), ("claim", "claim")),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
    "complaint.resolved": (("party", "complainant"), ("complaint", "case")),
    "complaint.escalated": (("party", "complainant"), ("complaint", "case")),
}

EN = {
    "offer.viewed": "The traveller viewed a fare offer.",
    "order.created": "A booking was created.",
    "payment.captured": "The payment went through.",
    "payment.failed": "The payment failed.",
    "order.expired": "The booking expired unpaid.",
    "order.confirmed": "The booking was ticketed.",
    "seat.selected": "A seat was chosen.",
    "bag.added": "A checked bag was added.",
    "change.requested": "A change to the booking was requested.",
    "order.changed": "The booking was changed.",
    "change.declined": "The change was declined.",
    "order.cancelled": "The passenger cancelled the booking.",
    "refund.issued": "A refund was issued.",
    "loyalty.enrolled": "The traveller joined the loyalty programme.",
    "passenger.checked_in": "The passenger checked in.",
    "bag.dropped": "The bag was dropped at the airport.",
    "passenger.boarded": "The passenger boarded.",
    "passenger.no_show": "The passenger did not show up for the flight.",
    "boarding.denied": "The passenger was denied boarding on an overbooked flight.",
    "flight.delayed": "The flight was delayed.",
    "flight.cancelled": "The flight was cancelled.",
    "order.rebooked": "The passenger was rebooked on another flight.",
    "flight.departed": "The flight departed.",
    "flight.arrived": "The flight arrived on time.",
    "flight.arrived_late": "The flight arrived more than three hours late.",
    "bag.delivered": "The bag arrived on the belt.",
    "bag.delayed": "The bag did not arrive with the flight.",
    "bag.returned": "The delayed bag was delivered to the passenger.",
    "miles.credited": "Miles were credited for the flight.",
    "compensation.claimed": "The passenger claimed compensation.",
    "compensation.paid": "Compensation was paid.",
    "compensation.rejected": "The compensation claim was rejected.",
    "complaint.received": "A complaint was received.",
    "complaint.resolved": "The complaint was resolved.",
    "complaint.escalated": "The complaint went to the dispute resolution body.",
}
TR = {
    "offer.viewed": "Yolcu bir uçuş teklifini inceledi.",
    "order.created": "Rezervasyon oluşturuldu.",
    "payment.captured": "Ödeme alındı.",
    "payment.failed": "Ödeme başarısız oldu.",
    "order.expired": "Rezervasyon ödenmeden süresi doldu.",
    "order.confirmed": "Bilet düzenlendi.",
    "seat.selected": "Koltuk seçildi.",
    "bag.added": "Bagaj hakkı eklendi.",
    "change.requested": "Rezervasyonda değişiklik talep edildi.",
    "order.changed": "Rezervasyon değiştirildi.",
    "change.declined": "Değişiklik talebi reddedildi.",
    "order.cancelled": "Yolcu rezervasyonu iptal etti.",
    "refund.issued": "İade yapıldı.",
    "loyalty.enrolled": "Yolcu sadakat programına katıldı.",
    "passenger.checked_in": "Yolcu check-in yaptı.",
    "bag.dropped": "Bagaj havalimanında teslim edildi.",
    "passenger.boarded": "Yolcu uçağa bindi.",
    "passenger.no_show": "Yolcu uçuşa gelmedi.",
    "boarding.denied": "Fazla rezervasyon nedeniyle yolcunun binişi reddedildi.",
    "flight.delayed": "Uçuş rötar yaptı.",
    "flight.cancelled": "Uçuş iptal edildi.",
    "order.rebooked": "Yolcu başka bir uçuşa aktarıldı.",
    "flight.departed": "Uçak kalktı.",
    "flight.arrived": "Uçak zamanında indi.",
    "flight.arrived_late": "Uçak üç saatten fazla gecikmeyle indi.",
    "bag.delivered": "Bagaj banttan teslim alındı.",
    "bag.delayed": "Bagaj uçuşla birlikte gelmedi.",
    "bag.returned": "Geciken bagaj yolcuya teslim edildi.",
    "miles.credited": "Uçuş için mil yüklendi.",
    "compensation.claimed": "Yolcu tazminat talep etti.",
    "compensation.paid": "Tazminat ödendi.",
    "compensation.rejected": "Tazminat talebi reddedildi.",
    "complaint.received": "Şikayet kaydı açıldı.",
    "complaint.resolved": "Şikayet çözüldü.",
    "complaint.escalated": "Şikayet uyuşmazlık çözüm mercine taşındı.",
}

# IATA NDC and ONE Order service areas, with the action terms the episode builder uses for every pack.
OPERATIONS = {
    "offer.viewed": ("Offer Management", "Retrieve"),
    "order.created": ("Order Management", "Initiate"),
    "payment.captured": ("Payment", "Execute"),
    "payment.failed": ("Payment", "Execute"),
    "order.expired": ("Order Management", "Control"),
    "order.confirmed": ("Order Management", "Execute"),
    "seat.selected": ("Ancillary Servicing", "Update"),
    "bag.added": ("Ancillary Servicing", "Capture"),
    "change.requested": ("Order Change", "Request"),
    "order.changed": ("Order Change", "Evaluate"),
    "change.declined": ("Order Change", "Evaluate"),
    "order.cancelled": ("Order Change", "Control"),
    "refund.issued": ("Refund", "Execute"),
    "loyalty.enrolled": ("Loyalty", "Initiate"),
    "passenger.checked_in": ("Departure Control", "Initiate"),
    "bag.dropped": ("Baggage Handling", "Capture"),
    "passenger.boarded": ("Departure Control", "Evaluate"),
    "passenger.no_show": ("Departure Control", "Evaluate"),
    "boarding.denied": ("Departure Control", "Evaluate"),
    "flight.delayed": ("Flight Operations", "Update"),
    "flight.cancelled": ("Flight Operations", "Control"),
    "order.rebooked": ("Disruption Management", "Execute"),
    "flight.departed": ("Flight Operations", "Execute"),
    "flight.arrived": ("Flight Operations", "Capture"),
    "flight.arrived_late": ("Flight Operations", "Capture"),
    "bag.delivered": ("Baggage Handling", "Evaluate"),
    "bag.delayed": ("Baggage Handling", "Evaluate"),
    "bag.returned": ("Baggage Tracing", "Execute"),
    "miles.credited": ("Loyalty", "Capture"),
    "compensation.claimed": ("Passenger Claims", "Initiate"),
    "compensation.paid": ("Passenger Claims", "Execute"),
    "compensation.rejected": ("Passenger Claims", "Execute"),
    "complaint.received": ("Customer Case Management", "Initiate"),
    "complaint.resolved": ("Customer Case Management", "Execute"),
    "complaint.escalated": ("Customer Case Management", "Execute"),
}

TRAJECTORY_TYPES = (
    "booking_lapsed",
    "no_show",
    "denied_boarding",
    "cancelled_flight",
    "compensation_claim",
    "delayed_flight",
    "baggage_issue",
    "voluntary_cancellation",
    "itinerary_change",
    "complaint_case",
    "completed_trip",
    "booked",
)


def classify(types: list[str]) -> str:
    present = set(types)
    if present & {"payment.failed", "order.expired"}:
        return "booking_lapsed"
    if "passenger.no_show" in present:
        return "no_show"
    if "boarding.denied" in present:
        return "denied_boarding"
    if "flight.cancelled" in present:
        return "cancelled_flight"
    if "compensation.claimed" in present:
        return "compensation_claim"
    if present & {"flight.delayed", "flight.arrived_late"}:
        return "delayed_flight"
    if "bag.delayed" in present:
        return "baggage_issue"
    if "order.cancelled" in present:
        return "voluntary_cancellation"
    if "change.requested" in present:
        return "itinerary_change"
    if "complaint.received" in present:
        return "complaint_case"
    if present & {"flight.arrived", "flight.arrived_late"}:
        return "completed_trip"
    return "booked"


FAILED_OUTCOMES = frozenset(
    {"payment.failed", "order.expired", "passenger.no_show", "order.cancelled", "change.declined", "compensation.rejected", "complaint.escalated"}
)


def success(types: list[str]) -> bool:
    if not types or FAILED_OUTCOMES & set(types):
        return False
    # A disruption ends well only when the passenger is rebooked, and a late arrival only once compensation is paid.
    disruptions = [name for name in types if name in {"flight.cancelled", "boarding.denied", "order.rebooked", "refund.issued"}]
    if disruptions and disruptions[-1] in {"flight.cancelled", "boarding.denied", "refund.issued"}:
        return False
    if "flight.arrived_late" in types and "compensation.paid" not in types[types.index("flight.arrived_late"):]:
        return False
    bags = [name for name in types if name in {"bag.delayed", "bag.returned"}]
    return not bags or bags[-1] == "bag.returned"


PROMPTS = {
    "en": (
        "Simulate a synthetic airline passenger journey. Do not invent real people, flight numbers, or booking references.",
        "Narrate a synthetic air travel journey step by step, from booking to arrival. Keep every reference synthetic.",
        "Walk through a simulated airline case from the first fare search onward. Use no real names or numbers.",
    ),
    "tr": (
        "Sentetik bir havayolu yolcu yolculuğu üret. Gerçek kişi, uçuş numarası veya rezervasyon kodu uydurma.",
        "Rezervasyondan varışa kadar sentetik bir uçak yolculuğunu adım adım anlat. Tüm referanslar sentetik olsun.",
        "İlk bilet aramasından başlayarak simüle edilmiş bir havayolu vakasını anlat. Gerçek isim ya da numara kullanma.",
    ),
}

OPENINGS = {
    "en": {
        "*": ("I want to book a flight.", "I need a flight next month.", "I would like to book an economy seat."),
        "itinerary_change": ("I want to book a flight, but my dates may change.", "Can I book a flight I might need to move?"),
    },
    "tr": {
        "*": ("Uçak bileti almak istiyorum.", "Gelecek ay için bir uçuşa ihtiyacım var.", "Ekonomi sınıfında bir koltuk ayırtmak istiyorum."),
        "itinerary_change": ("Bilet almak istiyorum ama tarihlerim değişebilir.", "Tarihini değiştirebileceğim bir bilet alabilir miyim?"),
    },
}

FOLLOW_UPS = {
    "en": ("What happened next?", "Go on.", "And then?", "What did the airline do after that?", "Continue, please."),
    "tr": ("Sonra ne oldu?", "Devam edin.", "Ardından?", "Havayolu bundan sonra ne yaptı?", "Lütfen devam edin."),
}


def subtype(kind: str, default: str, steering: Any) -> str:
    if kind in {"offering", "order"} and steering.products:
        return steering.products[0]
    return default


PACK = PackSpec(
    sector="airline",
    generator_id=GENERATOR_ID,
    pack_version=PACK_VERSION,
    lifecycle=LIFECYCLE,
    default_domain=SB,
    languages=("en", "tr"),
    objects=OBJECTS,
    roles=ROLES,
    phrases={"en": EN, "tr": TR},
    prompts=PROMPTS,
    openings=OPENINGS,
    follow_ups=FOLLOW_UPS,
    relationships=(
        ("party", "HOLDS", "order"),
        ("order", "BOOKED_ON", "flight"),
        ("bag", "TRAVELS_ON", "flight"),
        ("party", "FILED", "claim"),
        ("claim", "CONCERNS", "order"),
        ("party", "RAISED", "complaint"),
    ),
    system_events=frozenset(
        {
            "payment.captured",
            "payment.failed",
            "order.expired",
            "order.confirmed",
            "order.changed",
            "change.declined",
            "refund.issued",
            "passenger.no_show",
            "flight.delayed",
            "flight.cancelled",
            "order.rebooked",
            "flight.departed",
            "flight.arrived",
            "flight.arrived_late",
            "bag.delivered",
            "bag.delayed",
            "bag.returned",
            "miles.credited",
            "compensation.paid",
            "compensation.rejected",
            "complaint.resolved",
            "complaint.escalated",
        }
    ),
    fixed_channels={"bag.dropped": "airport", "passenger.boarded": "airport", "boarding.denied": "airport"},
    default_channels={
        "offer.viewed": "web",
        "order.created": "web",
        "seat.selected": "app",
        "bag.added": "app",
        "change.requested": "web",
        "order.cancelled": "web",
        "loyalty.enrolled": "web",
        "passenger.checked_in": "app",
        "compensation.claimed": "web",
        "complaint.received": "call_centre",
    },
    amounts={
        "payment.captured": Amount(40.0, 900.0, "debit", "fare_payment"),
        "seat.selected": Amount(5.0, 40.0, "debit", "seat_fee"),
        "bag.added": Amount(15.0, 70.0, "debit", "bag_fee"),
        "refund.issued": Amount(40.0, 900.0, "credit", "refund"),
        "compensation.paid": Amount(220.0, 600.0, "credit", "compensation"),
    },
    qualifiers={
        ("order.created", "flight"): "booked_flight",
        ("order.rebooked", "flight"): "rebooked_flight",
        ("bag.dropped", "flight"): "carrying_flight",
        ("compensation.claimed", "order"): "disrupted_order",
    },
    effective_lag_hours={
        # A refund or compensation reaches the passenger's card days after it is issued.
        "refund.issued": (DAY, 7 * DAY),
        "compensation.paid": (DAY, 7 * DAY),
        "payment.captured": (0.1, 48.0),
    },
    trajectory_types=TRAJECTORY_TYPES,
    classify=classify,
    success=success,
    subtype=subtype,
    correctness_drops=("boarding.denied",),
    goal={
        "en": "The passenger travels as booked or rebooked without a failed outcome: no failed payment, expired or cancelled booking, no-show, declined change, refund after a disruption, late arrival without compensation, rejected claim, undelivered bag, or escalated complaint.",
        "tr": "Yolcu, başarısız bir sonuç olmadan rezervasyonuyla ya da aktarıldığı uçuşla seyahat eder: başarısız ödeme, süresi dolan veya iptal edilen rezervasyon, uçuşa gelmeme, reddedilen değişiklik, aksaklık sonrası iade, tazminatsız gecikmeli varış, reddedilen talep, teslim edilmeyen bagaj veya üst mercie taşınan şikâyet olmaz.",
    },
    operations=OPERATIONS,
    agent={
        "en": {
            "system": "You are an operations agent at an airline. Use only the listed operations and only the case's own identifiers.",
            "task": "You operate the airline's systems for passenger {party}. {situation} Decide the next step and record it with one operation call, then say what you did.",
        },
        "tr": {
            "system": "Bir havayolu şirketinde operasyon temsilcisisiniz. Yalnızca listelenen işlemleri ve yalnızca bu vakanın kimliklerini kullanın.",
            "task": "{party} numaralı yolcu için havayolunun sistemlerini yönetiyorsunuz. {situation} Sıradaki adıma karar verin, tek bir işlem çağrısıyla kaydedin ve ne yaptığınızı söyleyin.",
        },
    },
)
