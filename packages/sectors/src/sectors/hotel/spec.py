"""Hotel guest journeys as orthogonal state machines, in the vocabulary of HTNG and OpenTravel.

Dimensions: relationship and loyalty on the party; the reservation with its guarantee, modification,
and cancellation terms; the stay with its room, upgrade, service requests, and room issues; the folio
with its settlement; the review; and the complaint case. An oversold house walks a guest to another
hotel. Weights are hand-set priors until data sources calibrate them.
"""

from __future__ import annotations

from typing import Any

from sectors.journeys import Amount, PackSpec
from sectors.lifecycle import EventSpec, LifecycleSpec, need, put

GENERATOR_ID = "hotel-semi-markov-v1"
PACK_VERSION = "hotel-pack-1"

BR = "booking_and_reservations"
MC = "modifications_and_cancellations"
AR = "arrival_and_check_in"
IS = "in_stay_services"
CB = "check_out_and_billing"
LR = "loyalty_and_reviews"
CO = "complaints"

DAY = 24.0
BEFORE_ARRIVAL = (None,)
AFTER_STAY = ("departed", "relocated")

LIFECYCLE = LifecycleSpec(
    object_types={
        "party": "party",
        "offering": "rate_plan",
        "reservation": "reservation",
        "stay": "stay",
        "folio": "folio",
        "complaint": "complaint",
    },
    events=(
        EventSpec(
            "offer.viewed", (BR,),
            requires=(need("party", "relationship", None),),
            sets=(put("party", "relationship", "prospect"),),
            dwell_hours=(0.0, 0.0), opening=True,
        ),
        EventSpec(
            "reservation.created", (BR,),
            requires=(need("party", "relationship", "prospect"), need("reservation", "reservation", None)),
            sets=(put("reservation", "reservation", "created"),),
            dwell_hours=(0.05, 1.0),
            violation="reservation created before a rate was viewed",
        ),
        EventSpec(
            "guarantee.accepted", (BR,),
            requires=(need("reservation", "reservation", "created"), need("reservation", "guarantee", None)),
            sets=(put("reservation", "guarantee", "accepted"),),
            weight=0.9, outcome="guarantee_outcome", dwell_hours=(0.01, 0.5),
            violation="guarantee accepted outside an unguaranteed reservation",
        ),
        EventSpec(
            "guarantee.declined", (BR,),
            requires=(need("reservation", "reservation", "created"), need("reservation", "guarantee", None)),
            sets=(put("reservation", "guarantee", "declined"),),
            weight=0.05, outcome="guarantee_outcome", ends_journey=True, dwell_hours=(0.01, 0.5),
            violation="guarantee declined outside an unguaranteed reservation",
        ),
        EventSpec(
            "reservation.lapsed", (BR,),
            requires=(need("reservation", "reservation", "created"), need("reservation", "guarantee", None)),
            sets=(put("reservation", "reservation", "lapsed"),),
            weight=0.05, outcome="guarantee_outcome", ends_journey=True, dwell_hours=(DAY, 3 * DAY),
            violation="reservation lapsed after it was guaranteed",
        ),
        EventSpec(
            "reservation.confirmed", (BR,),
            requires=(need("reservation", "reservation", "created"), need("reservation", "guarantee", "accepted")),
            sets=(put("reservation", "reservation", "confirmed"), put("party", "relationship", "guest")),
            dwell_hours=(0.01, 1.0),
            violation="reservation confirmed before it was guaranteed",
        ),
        EventSpec(
            "modification.requested", (MC,),
            requires=(
                need("reservation", "reservation", "confirmed"), need("stay", "stay", *BEFORE_ARRIVAL),
                need("reservation", "modification", None, "modified", "declined"),
            ),
            sets=(put("reservation", "modification", "requested"),),
            weight=0.12, repeat=2, dwell_hours=(DAY, 30 * DAY),
            violation="modification requested before confirmation or after arrival",
        ),
        EventSpec(
            "reservation.modified", (MC,),
            requires=(need("reservation", "modification", "requested"),),
            sets=(put("reservation", "modification", "modified"),),
            weight=0.82, repeat=2, outcome="modification_decision", dwell_hours=(0.1, 24.0),
            violation="reservation modified without a request",
        ),
        EventSpec(
            "modification.declined", (MC,),
            requires=(need("reservation", "modification", "requested"),),
            sets=(put("reservation", "modification", "declined"),),
            weight=0.18, repeat=2, outcome="modification_decision", dwell_hours=(0.1, 24.0),
            violation="modification declined without a request",
        ),
        EventSpec(
            "reservation.cancelled", (MC,),
            requires=(need("reservation", "reservation", "confirmed"), need("stay", "stay", *BEFORE_ARRIVAL)),
            sets=(put("reservation", "reservation", "cancelled"),),
            weight=0.05, dwell_hours=(DAY, 30 * DAY),
            violation="reservation cancelled before confirmation or after arrival",
        ),
        EventSpec(
            "refund.issued", (MC,),
            requires=(need("reservation", "reservation", "cancelled"), need("reservation", "terms", None)),
            sets=(put("reservation", "terms", "refunded"),),
            weight=0.7, outcome="cancellation_terms", dwell_hours=(DAY, 10 * DAY),
            violation="refund issued for a reservation that was not cancelled",
        ),
        EventSpec(
            "cancellation.fee_charged", (MC,),
            requires=(need("reservation", "reservation", "cancelled"), need("reservation", "terms", None)),
            sets=(put("reservation", "terms", "charged"),),
            weight=0.3, outcome="cancellation_terms", dwell_hours=(0.1, 24.0),
            violation="cancellation fee charged for a reservation that was not cancelled",
        ),
        EventSpec(
            "loyalty.enrolled", (LR,),
            requires=(need("party", "relationship", "prospect", "guest"), need("party", "loyalty", None)),
            sets=(put("party", "loyalty", "member"),),
            weight=0.15, dwell_hours=(0.1, 30 * DAY),
            violation="loyalty enrolment twice",
        ),
        EventSpec(
            "room.assigned", (AR,),
            requires=(need("reservation", "reservation", "confirmed"), need("stay", "stay", None)),
            sets=(put("stay", "stay", "assigned"),),
            dwell_hours=(DAY, 45 * DAY),
            violation="room assigned before the reservation was confirmed",
        ),
        EventSpec(
            "room.upgraded", (AR,),
            requires=(need("stay", "stay", "assigned", "in_house"), need("stay", "upgrade", None)),
            sets=(put("stay", "upgrade", "upgraded"),),
            weight=0.1, dwell_hours=(0.1, 6.0),
            violation="room upgraded before a room was assigned or after departure",
        ),
        EventSpec(
            "guest.checked_in", (AR,),
            requires=(need("stay", "stay", "assigned"),),
            sets=(put("stay", "stay", "in_house"),),
            weight=0.93, outcome="arrival_outcome", dwell_hours=(0.2, 6.0),
            violation="check-in before a room was assigned",
        ),
        EventSpec(
            "guest.no_show", (AR,),
            requires=(need("stay", "stay", "assigned"),),
            sets=(put("stay", "stay", "no_show"),),
            weight=0.05, outcome="arrival_outcome", ends_journey=True, dwell_hours=(6.0, 24.0),
            violation="no-show recorded before a room was assigned",
        ),
        EventSpec(
            "guest.walked", (AR,),
            # An oversold house sends the guest to another hotel on arrival.
            requires=(need("stay", "stay", "assigned"),),
            sets=(put("stay", "stay", "relocated"),),
            weight=0.02, outcome="arrival_outcome", dwell_hours=(0.5, 3.0),
            violation="guest walked before a room was assigned",
        ),
        EventSpec(
            "relocation.compensated", (AR,),
            requires=(need("stay", "stay", "relocated"), need("stay", "compensation", None)),
            sets=(put("stay", "compensation", "paid"),),
            dwell_hours=(0.5, 24.0),
            violation="relocation compensated for a guest who was not walked",
        ),
        EventSpec(
            "service.requested", (IS,),
            requires=(need("stay", "stay", "in_house"), need("stay", "service", None, "fulfilled", "delayed")),
            sets=(put("stay", "service", "requested"),),
            weight=0.35, repeat=3, dwell_hours=(1.0, 48.0),
            violation="service requested outside a stay or while another request is open",
        ),
        EventSpec(
            "service.fulfilled", (IS,),
            requires=(need("stay", "service", "requested"),),
            sets=(put("stay", "service", "fulfilled"),),
            weight=0.88, repeat=3, outcome="service_outcome", dwell_hours=(0.2, 2.0),
            violation="service fulfilled without a request",
        ),
        EventSpec(
            "service.delayed", (IS,),
            requires=(need("stay", "service", "requested"),),
            sets=(put("stay", "service", "delayed"),),
            weight=0.12, repeat=3, outcome="service_outcome", dwell_hours=(2.0, 8.0),
            violation="service delayed without a request",
        ),
        EventSpec(
            "issue.reported", (IS,),
            requires=(need("stay", "stay", "in_house"), need("stay", "issue", None)),
            sets=(put("stay", "issue", "reported"),),
            weight=0.1, dwell_hours=(1.0, 72.0),
            violation="room issue reported outside a stay",
        ),
        EventSpec(
            "issue.fixed", (IS,),
            requires=(need("stay", "issue", "reported"),),
            sets=(put("stay", "issue", "fixed"),),
            weight=0.7, outcome="issue_resolution", dwell_hours=(0.5, 6.0),
            violation="room issue fixed before it was reported",
        ),
        EventSpec(
            "room.moved", (IS,),
            requires=(need("stay", "issue", "reported"),),
            sets=(put("stay", "issue", "moved"),),
            weight=0.3, outcome="issue_resolution", dwell_hours=(0.5, 4.0),
            violation="room move without a reported issue",
        ),
        EventSpec(
            "charge.posted", (IS, CB),
            requires=(need("stay", "stay", "in_house"),),
            sets=(put("folio", "folio", "open"),),
            weight=0.45, repeat=4, dwell_hours=(1.0, 48.0),
            violation="charge posted outside a stay",
        ),
        EventSpec(
            "guest.checked_out", (CB,),
            requires=(need("stay", "stay", "in_house"),),
            sets=(put("stay", "stay", "departed"),),
            weight=0.6, dwell_hours=(DAY, 7 * DAY),
            violation="check-out before check-in",
        ),
        EventSpec(
            "folio.settled", (CB,),
            requires=(need("stay", "stay", "departed"), need("folio", "settlement", None)),
            sets=(put("folio", "settlement", "paid"),),
            weight=0.93, outcome="settlement", dwell_hours=(0.05, 1.0),
            violation="folio settled before check-out",
        ),
        EventSpec(
            "folio.disputed", (CB,),
            requires=(need("stay", "stay", "departed"), need("folio", "settlement", None)),
            sets=(put("folio", "settlement", "disputed"),),
            weight=0.07, outcome="settlement", dwell_hours=(0.1, 72.0),
            violation="folio disputed before check-out",
        ),
        EventSpec(
            "folio.adjusted", (CB,),
            requires=(need("folio", "settlement", "disputed"),),
            sets=(put("folio", "settlement", "adjusted"),),
            weight=0.7, outcome="dispute_outcome", dwell_hours=(DAY, 10 * DAY),
            violation="folio adjusted without a dispute",
        ),
        EventSpec(
            "dispute.rejected", (CB,),
            requires=(need("folio", "settlement", "disputed"),),
            sets=(put("folio", "settlement", "rejected"),),
            weight=0.3, outcome="dispute_outcome", dwell_hours=(DAY, 10 * DAY),
            violation="dispute rejected without a dispute",
        ),
        EventSpec(
            "points.credited", (LR,),
            requires=(need("party", "loyalty", "member"), need("folio", "settlement", "paid", "adjusted"), need("stay", "points", None)),
            sets=(put("stay", "points", "credited"),),
            dwell_hours=(DAY, 7 * DAY),
            violation="points credited before the folio was settled or to a non-member",
        ),
        EventSpec(
            "review.positive", (LR,),
            requires=(need("stay", "stay", *AFTER_STAY), need("stay", "review", None)),
            sets=(put("stay", "review", "positive"),),
            weight=0.24, outcome="review_sentiment", dwell_hours=(DAY, 14 * DAY),
            violation="review posted before the stay ended",
        ),
        EventSpec(
            "review.negative", (LR,),
            requires=(need("stay", "stay", *AFTER_STAY), need("stay", "review", None)),
            sets=(put("stay", "review", "negative"),),
            weight=0.06, outcome="review_sentiment", dwell_hours=(DAY, 14 * DAY),
            violation="review posted before the stay ended",
        ),
        EventSpec(
            "complaint.received", (CO,),
            requires=(need("party", "relationship", "guest"), need("complaint", "complaint", None)),
            sets=(put("complaint", "complaint", "open"),),
            weight=0.05, dwell_hours=(DAY, 30 * DAY),
            violation="complaint received twice or before a reservation was confirmed",
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
        BR: ("reservation.confirmed", "guarantee.declined", "reservation.lapsed"),
        MC: ("reservation.modified", "modification.declined", "refund.issued", "cancellation.fee_charged"),
        AR: ("guest.checked_in", "guest.no_show", "guest.walked"),
        IS: ("service.fulfilled", "service.delayed", "issue.fixed", "room.moved"),
        CB: ("folio.settled", "folio.adjusted", "dispute.rejected"),
        LR: ("loyalty.enrolled", "review.positive", "review.negative"),
        CO: ("complaint.received",),
    },
)

OBJECTS = {
    "party": ("P", "party", "individual"),
    "offering": ("F", "rate_plan", "flexible"),
    "reservation": ("V", "reservation", "flexible"),
    "stay": ("Y", "stay", "standard_room"),
    "folio": ("B", "folio", "guest"),
    "complaint": ("M", "complaint", "service"),
}

GUEST = ("party", "guest")
ROLES = {
    "offer.viewed": (("party", "prospect"), ("offering", "rate_plan")),
    "reservation.created": (GUEST, ("reservation", "reservation")),
    "guarantee.accepted": (GUEST, ("reservation", "reservation")),
    "guarantee.declined": (GUEST, ("reservation", "reservation")),
    "reservation.lapsed": (GUEST, ("reservation", "reservation")),
    "reservation.confirmed": (GUEST, ("reservation", "reservation")),
    "modification.requested": (GUEST, ("reservation", "reservation")),
    "reservation.modified": (GUEST, ("reservation", "reservation")),
    "modification.declined": (GUEST, ("reservation", "reservation")),
    "reservation.cancelled": (GUEST, ("reservation", "reservation")),
    "refund.issued": (GUEST, ("reservation", "reservation")),
    "cancellation.fee_charged": (GUEST, ("reservation", "reservation")),
    "loyalty.enrolled": (("party", "member"),),
    "room.assigned": (GUEST, ("reservation", "reservation"), ("stay", "stay")),
    "room.upgraded": (GUEST, ("stay", "stay")),
    "guest.checked_in": (GUEST, ("stay", "stay")),
    "guest.no_show": (GUEST, ("stay", "stay"), ("reservation", "reservation")),
    "guest.walked": (GUEST, ("stay", "stay"), ("reservation", "reservation")),
    "relocation.compensated": (GUEST, ("stay", "stay")),
    "service.requested": (GUEST, ("stay", "stay")),
    "service.fulfilled": (GUEST, ("stay", "stay")),
    "service.delayed": (GUEST, ("stay", "stay")),
    "issue.reported": (GUEST, ("stay", "stay")),
    "issue.fixed": (GUEST, ("stay", "stay")),
    "room.moved": (GUEST, ("stay", "stay")),
    "charge.posted": (GUEST, ("stay", "stay"), ("folio", "folio")),
    "guest.checked_out": (GUEST, ("stay", "stay")),
    "folio.settled": (GUEST, ("folio", "folio"), ("stay", "stay")),
    "folio.disputed": (GUEST, ("folio", "folio")),
    "folio.adjusted": (GUEST, ("folio", "folio")),
    "dispute.rejected": (GUEST, ("folio", "folio")),
    "points.credited": (("party", "member"), ("stay", "stay")),
    "review.positive": (("party", "reviewer"), ("stay", "stay")),
    "review.negative": (("party", "reviewer"), ("stay", "stay")),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
    "complaint.resolved": (("party", "complainant"), ("complaint", "case")),
    "complaint.escalated": (("party", "complainant"), ("complaint", "case")),
}

EN = {
    "offer.viewed": "The traveller viewed a room rate.",
    "reservation.created": "A reservation was created.",
    "guarantee.accepted": "The card guarantee was accepted.",
    "guarantee.declined": "The card guarantee was declined.",
    "reservation.lapsed": "The reservation lapsed without a guarantee.",
    "reservation.confirmed": "The reservation was confirmed.",
    "modification.requested": "A change to the reservation was requested.",
    "reservation.modified": "The reservation was changed.",
    "modification.declined": "The change was declined.",
    "reservation.cancelled": "The guest cancelled the reservation.",
    "refund.issued": "The prepayment was refunded.",
    "cancellation.fee_charged": "A late-cancellation fee was charged.",
    "loyalty.enrolled": "The traveller joined the loyalty programme.",
    "room.assigned": "A room was assigned.",
    "room.upgraded": "The room was upgraded.",
    "guest.checked_in": "The guest checked in.",
    "guest.no_show": "The guest did not arrive.",
    "guest.walked": "The hotel was full, and the guest was sent to another hotel.",
    "relocation.compensated": "The guest was compensated for the relocation.",
    "service.requested": "The guest asked for a service.",
    "service.fulfilled": "The request was fulfilled.",
    "service.delayed": "The request was delayed.",
    "issue.reported": "A problem with the room was reported.",
    "issue.fixed": "The room problem was fixed.",
    "room.moved": "The guest was moved to another room.",
    "charge.posted": "A charge was posted to the folio.",
    "guest.checked_out": "The guest checked out.",
    "folio.settled": "The folio was settled.",
    "folio.disputed": "The guest disputed the folio.",
    "folio.adjusted": "The folio was adjusted.",
    "dispute.rejected": "The dispute was rejected.",
    "points.credited": "Points were credited for the stay.",
    "review.positive": "The guest left a positive review.",
    "review.negative": "The guest left a negative review.",
    "complaint.received": "A complaint was received.",
    "complaint.resolved": "The complaint was resolved.",
    "complaint.escalated": "The complaint was escalated.",
}
TR = {
    "offer.viewed": "Misafir bir oda fiyatını inceledi.",
    "reservation.created": "Rezervasyon oluşturuldu.",
    "guarantee.accepted": "Kartla garanti kabul edildi.",
    "guarantee.declined": "Kartla garanti reddedildi.",
    "reservation.lapsed": "Rezervasyon garanti verilmediği için düştü.",
    "reservation.confirmed": "Rezervasyon onaylandı.",
    "modification.requested": "Rezervasyonda değişiklik talep edildi.",
    "reservation.modified": "Rezervasyon değiştirildi.",
    "modification.declined": "Değişiklik talebi reddedildi.",
    "reservation.cancelled": "Misafir rezervasyonu iptal etti.",
    "refund.issued": "Ön ödeme iade edildi.",
    "cancellation.fee_charged": "Geç iptal ücreti alındı.",
    "loyalty.enrolled": "Misafir sadakat programına katıldı.",
    "room.assigned": "Oda atandı.",
    "room.upgraded": "Oda bir üst kategoriye yükseltildi.",
    "guest.checked_in": "Misafir giriş yaptı.",
    "guest.no_show": "Misafir gelmedi.",
    "guest.walked": "Otel dolu olduğu için misafir başka bir otele yönlendirildi.",
    "relocation.compensated": "Başka otele yönlendirilen misafire tazminat verildi.",
    "service.requested": "Misafir bir hizmet talep etti.",
    "service.fulfilled": "Talep karşılandı.",
    "service.delayed": "Talep gecikti.",
    "issue.reported": "Odayla ilgili bir sorun bildirildi.",
    "issue.fixed": "Odadaki sorun giderildi.",
    "room.moved": "Misafir başka bir odaya taşındı.",
    "charge.posted": "Folyoya bir harcama işlendi.",
    "guest.checked_out": "Misafir çıkış yaptı.",
    "folio.settled": "Folyo ödendi.",
    "folio.disputed": "Misafir folyoya itiraz etti.",
    "folio.adjusted": "Folyo düzeltildi.",
    "dispute.rejected": "İtiraz reddedildi.",
    "points.credited": "Konaklama için puan yüklendi.",
    "review.positive": "Misafir olumlu bir değerlendirme yazdı.",
    "review.negative": "Misafir olumsuz bir değerlendirme yazdı.",
    "complaint.received": "Şikayet kaydı açıldı.",
    "complaint.resolved": "Şikayet çözüldü.",
    "complaint.escalated": "Şikayet üst mercie taşındı.",
}

# HTNG and OpenTravel service areas, with the action terms the episode builder uses for every pack.
OPERATIONS = {
    "offer.viewed": ("Availability", "Retrieve"),
    "reservation.created": ("Reservation", "Initiate"),
    "guarantee.accepted": ("Payment Guarantee", "Evaluate"),
    "guarantee.declined": ("Payment Guarantee", "Evaluate"),
    "reservation.lapsed": ("Reservation", "Control"),
    "reservation.confirmed": ("Reservation", "Execute"),
    "modification.requested": ("Reservation Modification", "Request"),
    "reservation.modified": ("Reservation Modification", "Evaluate"),
    "modification.declined": ("Reservation Modification", "Evaluate"),
    "reservation.cancelled": ("Cancellation", "Initiate"),
    "refund.issued": ("Cancellation", "Execute"),
    "cancellation.fee_charged": ("Cancellation", "Execute"),
    "loyalty.enrolled": ("Loyalty", "Initiate"),
    "room.assigned": ("Room Assignment", "Execute"),
    "room.upgraded": ("Room Assignment", "Update"),
    "guest.checked_in": ("Front Office", "Evaluate"),
    "guest.no_show": ("Front Office", "Evaluate"),
    "guest.walked": ("Front Office", "Evaluate"),
    "relocation.compensated": ("Guest Relocation", "Execute"),
    "service.requested": ("Service Request", "Initiate"),
    "service.fulfilled": ("Service Request", "Execute"),
    "service.delayed": ("Service Request", "Execute"),
    "issue.reported": ("Maintenance", "Initiate"),
    "issue.fixed": ("Maintenance", "Execute"),
    "room.moved": ("Maintenance", "Execute"),
    "charge.posted": ("Folio", "Capture"),
    "guest.checked_out": ("Front Office", "Control"),
    "folio.settled": ("Folio", "Execute"),
    "folio.disputed": ("Folio", "Execute"),
    "folio.adjusted": ("Folio", "Update"),
    "dispute.rejected": ("Folio", "Update"),
    "points.credited": ("Loyalty", "Capture"),
    "review.positive": ("Guest Feedback", "Capture"),
    "review.negative": ("Guest Feedback", "Capture"),
    "complaint.received": ("Customer Case Management", "Initiate"),
    "complaint.resolved": ("Customer Case Management", "Execute"),
    "complaint.escalated": ("Customer Case Management", "Execute"),
}

TRAJECTORY_TYPES = (
    "reservation_lapsed",
    "no_show",
    "walked",
    "cancelled_stay",
    "billing_dispute",
    "service_recovery",
    "modified_stay",
    "complaint_case",
    "completed_stay",
    "reserved",
)


def classify(types: list[str]) -> str:
    present = set(types)
    if present & {"guarantee.declined", "reservation.lapsed"}:
        return "reservation_lapsed"
    if "guest.no_show" in present:
        return "no_show"
    if "guest.walked" in present:
        return "walked"
    if "reservation.cancelled" in present:
        return "cancelled_stay"
    if "folio.disputed" in present:
        return "billing_dispute"
    if present & {"service.delayed", "issue.reported"}:
        return "service_recovery"
    if "modification.requested" in present:
        return "modified_stay"
    if "complaint.received" in present:
        return "complaint_case"
    if "guest.checked_out" in present:
        return "completed_stay"
    return "reserved"


FAILED_OUTCOMES = frozenset(
    {
        "guarantee.declined", "reservation.lapsed", "reservation.cancelled", "cancellation.fee_charged", "modification.declined",
        "guest.no_show", "guest.walked", "dispute.rejected", "review.negative", "complaint.escalated",
    }
)


def success(types: list[str]) -> bool:
    if not types or FAILED_OUTCOMES & set(types):
        return False
    # A delayed request is recovered only when a later one is fulfilled.
    services = [name for name in types if name in {"service.fulfilled", "service.delayed"}]
    return not services or services[-1] == "service.fulfilled"


PROMPTS = {
    "en": (
        "Simulate a synthetic hotel guest journey. Do not invent real people, hotels, or confirmation numbers.",
        "Narrate a synthetic hotel stay step by step, from booking to check-out. Keep every reference synthetic.",
        "Walk through a simulated hotel case from the first rate search onward. Use no real names or numbers.",
    ),
    "tr": (
        "Sentetik bir otel misafiri yolculuğu üret. Gerçek kişi, otel veya rezervasyon numarası uydurma.",
        "Rezervasyondan çıkışa kadar sentetik bir otel konaklamasını adım adım anlat. Tüm referanslar sentetik olsun.",
        "İlk fiyat aramasından başlayarak simüle edilmiş bir otel vakasını anlat. Gerçek isim ya da numara kullanma.",
    ),
}

OPENINGS = {
    "en": {
        "*": ("I want to book a room.", "Do you have a double room for three nights?", "I would like to reserve a room next month."),
        "modified_stay": ("I want to book a room, but my dates may change.", "Can I book a room and move the dates later?"),
    },
    "tr": {
        "*": ("Oda ayırtmak istiyorum.", "Üç gece için çift kişilik odanız var mı?", "Gelecek ay için oda rezervasyonu yapmak istiyorum."),
        "modified_stay": ("Oda ayırtmak istiyorum ama tarihlerim değişebilir.", "Oda ayırtıp tarihleri sonra değiştirebilir miyim?"),
    },
}

FOLLOW_UPS = {
    "en": ("What happened next?", "Go on.", "And then?", "What did the hotel do after that?", "Continue, please."),
    "tr": ("Sonra ne oldu?", "Devam edin.", "Ardından?", "Otel bundan sonra ne yaptı?", "Lütfen devam edin."),
}


def subtype(kind: str, default: str, steering: Any) -> str:
    if kind in {"offering", "reservation"} and steering.products:
        return steering.products[0]
    return default


PACK = PackSpec(
    sector="hotel",
    generator_id=GENERATOR_ID,
    pack_version=PACK_VERSION,
    lifecycle=LIFECYCLE,
    default_domain=BR,
    languages=("en", "tr"),
    objects=OBJECTS,
    roles=ROLES,
    phrases={"en": EN, "tr": TR},
    prompts=PROMPTS,
    openings=OPENINGS,
    follow_ups=FOLLOW_UPS,
    relationships=(
        ("party", "HOLDS", "reservation"),
        ("reservation", "RESULTED_IN", "stay"),
        ("folio", "BILLS", "stay"),
        ("party", "RAISED", "complaint"),
    ),
    system_events=frozenset(
        {
            "guarantee.accepted",
            "guarantee.declined",
            "reservation.lapsed",
            "reservation.confirmed",
            "reservation.modified",
            "modification.declined",
            "refund.issued",
            "cancellation.fee_charged",
            "room.assigned",
            "room.upgraded",
            "guest.no_show",
            "relocation.compensated",
            "service.fulfilled",
            "service.delayed",
            "issue.fixed",
            "room.moved",
            "charge.posted",
            "folio.settled",
            "folio.adjusted",
            "dispute.rejected",
            "points.credited",
            "complaint.resolved",
            "complaint.escalated",
        }
    ),
    fixed_channels={"guest.checked_in": "front_desk", "guest.checked_out": "front_desk", "guest.walked": "front_desk"},
    default_channels={
        "offer.viewed": "web",
        "reservation.created": "web",
        "modification.requested": "web",
        "reservation.cancelled": "web",
        "loyalty.enrolled": "web",
        "service.requested": "app",
        "issue.reported": "front_desk",
        "folio.disputed": "front_desk",
        "review.positive": "web",
        "review.negative": "web",
        "complaint.received": "front_desk",
    },
    amounts={
        "charge.posted": Amount(5.0, 150.0, "debit", "folio_charge"),
        "folio.settled": Amount(60.0, 1500.0, "debit", "room_and_charges"),
        "cancellation.fee_charged": Amount(40.0, 300.0, "debit", "cancellation_fee"),
        "refund.issued": Amount(40.0, 900.0, "credit", "refund"),
        "folio.adjusted": Amount(10.0, 200.0, "credit", "folio_adjustment"),
        "relocation.compensated": Amount(50.0, 300.0, "credit", "relocation_compensation"),
    },
    qualifiers={
        ("room.assigned", "reservation"): "fulfilled_reservation",
        ("charge.posted", "folio"): "charged_folio",
        ("folio.settled", "stay"): "billed_stay",
    },
    effective_lag_hours={
        # A card payment or refund reaches the account days after it is taken.
        "folio.settled": (0.1, 48.0),
        "refund.issued": (DAY, 7 * DAY),
        "folio.adjusted": (DAY, 7 * DAY),
    },
    trajectory_types=TRAJECTORY_TYPES,
    classify=classify,
    success=success,
    subtype=subtype,
    correctness_drops=("guest.walked",),
    goal={
        "en": "The guest stays as booked without a failed outcome: no declined guarantee, lapsed or cancelled reservation, late-cancellation fee, declined change, no-show, being walked to another hotel, delayed request left unrecovered, rejected billing dispute, negative review, or escalated complaint.",
        "tr": "Misafir, başarısız bir sonuç olmadan rezervasyonuna uygun konaklar: reddedilen garanti, düşen veya iptal edilen rezervasyon, geç iptal ücreti, reddedilen değişiklik, gelmeme, başka otele yönlendirilme, telafi edilmeyen gecikmiş talep, reddedilen folyo itirazı, olumsuz değerlendirme veya üst mercie taşınan şikâyet olmaz.",
    },
    operations=OPERATIONS,
    agent={
        "en": {
            "system": "You are an operations agent at a hotel. Use only the listed operations and only the case's own identifiers.",
            "task": "You operate the hotel's systems for guest {party}. {situation} Decide the next step and record it with one operation call, then say what you did.",
        },
        "tr": {
            "system": "Bir otelde operasyon temsilcisisiniz. Yalnızca listelenen işlemleri ve yalnızca bu vakanın kimliklerini kullanın.",
            "task": "{party} numaralı misafir için otelin sistemlerini yönetiyorsunuz. {situation} Sıradaki adıma karar verin, tek bir işlem çağrısıyla kaydedin ve ne yaptığınızı söyleyin.",
        },
    },
)
