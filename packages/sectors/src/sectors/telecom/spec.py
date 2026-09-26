"""Retail telecommunications as orthogonal state machines: mobile and broadband subscriptions.

Dimensions: relationship on the party; the order and its credit check; a number port; service,
billing, plan, and retention on the subscription; the trouble ticket; and the complaint case.
Operations are named after TM Forum Open API domains. Weights are hand-set priors until data
sources calibrate them.
"""

from __future__ import annotations

from typing import Any

from sectors.journeys import Amount, PackSpec
from sectors.lifecycle import EventSpec, LifecycleSpec, need, put

GENERATOR_ID = "telecom-semi-markov-v1"
PACK_VERSION = "telecom-pack-1"

SO = "sales_and_ordering"
AP = "activation_and_porting"
BP = "billing_and_payments"
PC = "plan_changes"
FM = "fault_management"
RE = "retention"
CO = "complaints"

DAY = 24.0
LIVE = ("active", "suspended")

LIFECYCLE = LifecycleSpec(
    object_types={
        "party": "party",
        "offering": "product_offering",
        "order": "product_order",
        "port": "port_request",
        "subscription": "subscription",
        "fault": "trouble_ticket",
        "complaint": "complaint",
    },
    events=(
        EventSpec(
            "product.viewed", (SO,),
            requires=(need("party", "relationship", None),),
            sets=(put("party", "relationship", "prospect"),),
            dwell_hours=(0.0, 0.0), opening=True,
        ),
        EventSpec(
            "order.started", (SO,),
            requires=(need("party", "relationship", "prospect"), need("order", "order", None)),
            sets=(put("order", "order", "started"),),
            dwell_hours=(0.05, 0.5),
            violation="order started before the plan was viewed",
        ),
        EventSpec(
            "order.submitted", (SO,),
            requires=(need("order", "order", "started"),),
            sets=(put("order", "order", "submitted"),),
            weight=0.88, outcome="order_completion", dwell_hours=(0.1, 2.0),
            violation="order submitted before it started",
        ),
        EventSpec(
            "order.abandoned", (SO,),
            requires=(need("order", "order", "started"),),
            sets=(put("order", "order", "abandoned"),),
            weight=0.12, outcome="order_completion", ends_journey=True, dwell_hours=(2.0, 7 * DAY),
            violation="order abandoned before it started or after it was submitted",
        ),
        EventSpec(
            "credit_check.started", (SO,),
            requires=(need("order", "order", "submitted"), need("order", "credit", None)),
            sets=(put("order", "credit", "pending"),),
            dwell_hours=(0.01, 0.2),
            violation="credit check started before the order was submitted",
        ),
        EventSpec(
            "credit_check.passed", (SO,),
            requires=(need("order", "credit", "pending"),),
            sets=(put("order", "credit", "passed"),),
            weight=0.8, outcome="credit_outcome", dwell_hours=(0.01, 0.5),
            violation="credit decided outside a pending credit check",
        ),
        EventSpec(
            "credit_check.deposit_required", (SO,),
            requires=(need("order", "credit", "pending"),),
            sets=(put("order", "credit", "deposit"),),
            weight=0.14, outcome="credit_outcome", dwell_hours=(0.05, 2.0),
            violation="credit decided outside a pending credit check",
        ),
        EventSpec(
            "credit_check.failed", (SO,),
            requires=(need("order", "credit", "pending"),),
            sets=(put("order", "credit", "failed"),),
            weight=0.06, outcome="credit_outcome", ends_journey=True, dwell_hours=(0.01, 0.5),
            violation="credit decided outside a pending credit check",
        ),
        EventSpec(
            "order.approved", (SO,),
            requires=(need("order", "order", "submitted"), need("order", "credit", "passed", "deposit")),
            sets=(put("order", "order", "approved"),),
            dwell_hours=(0.05, 4.0),
            violation="order approved before its credit check passed",
        ),
        EventSpec(
            "sim.dispatched", (AP,),
            requires=(need("order", "order", "approved"), need("subscription", "service", None)),
            sets=(put("subscription", "service", "provisioning"),),
            dwell_hours=(2.0, 48.0),
            violation="SIM dispatched before the order was approved",
        ),
        EventSpec(
            "port.requested", (AP,),
            # A port-in rides on a new connection: it is asked for before the line goes live.
            requires=(need("order", "order", "approved"), need("port", "port", None), need("subscription", "service", None, "provisioning")),
            sets=(put("port", "port", "requested"),),
            weight=0.35, dwell_hours=(0.1, 24.0),
            violation="number port requested before the order was approved or after the line went live",
        ),
        EventSpec(
            "port.completed", (AP,),
            requires=(need("port", "port", "requested"), need("subscription", "service", "provisioning")),
            sets=(put("port", "port", "completed"),),
            weight=0.85, outcome="port_outcome", dwell_hours=(DAY, 3 * DAY),
            violation="number ported before it was requested or before the SIM was sent",
        ),
        EventSpec(
            "port.failed", (AP,),
            requires=(need("port", "port", "requested"),),
            sets=(put("port", "port", "failed"),),
            weight=0.15, outcome="port_outcome", dwell_hours=(DAY, 3 * DAY),
            violation="number port failed before it was requested",
        ),
        EventSpec(
            "service.activated", (AP,),
            # A requested port settles before the line goes live, so the number is known.
            requires=(need("subscription", "service", "provisioning"), need("port", "port", None, "completed", "failed")),
            sets=(put("subscription", "service", "active"), put("party", "relationship", "customer")),
            dwell_hours=(1.0, 72.0),
            violation="service activated before provisioning or with a number port still open",
        ),
        EventSpec(
            "bill.issued", (BP,),
            requires=(need("subscription", "service", *LIVE), need("subscription", "billing", None, "paid")),
            sets=(put("subscription", "billing", "due"),),
            repeat=6, dwell_hours=(25 * DAY, 35 * DAY),
            violation="bill issued before activation or while another bill is unpaid",
        ),
        EventSpec(
            "bill.paid", (BP,),
            requires=(need("subscription", "billing", "due", "overdue"),),
            sets=(put("subscription", "billing", "paid"),),
            weight=0.9, repeat=6, outcome="payment_outcome", dwell_hours=(DAY, 20 * DAY),
            violation="bill paid before it was issued",
        ),
        EventSpec(
            "bill.overdue", (BP,),
            requires=(need("subscription", "billing", "due"),),
            sets=(put("subscription", "billing", "overdue"),),
            weight=0.1, repeat=3, outcome="payment_outcome", dwell_hours=(15 * DAY, 30 * DAY),
            violation="bill overdue before it was issued",
        ),
        EventSpec(
            "service.suspended", (BP,),
            requires=(need("subscription", "billing", "overdue"), need("subscription", "service", "active")),
            sets=(put("subscription", "service", "suspended"),),
            weight=0.6, repeat=2, dwell_hours=(DAY, 14 * DAY),
            violation="service suspended without an overdue bill",
        ),
        EventSpec(
            "service.restored", (BP,),
            requires=(need("subscription", "service", "suspended"), need("subscription", "billing", "paid")),
            sets=(put("subscription", "service", "active"),),
            repeat=2, dwell_hours=(0.5, 48.0),
            violation="service restored before the overdue bill was paid",
        ),
        EventSpec(
            "plan.change_requested", (PC,),
            requires=(need("subscription", "service", "active"), need("subscription", "plan", None, "changed", "declined")),
            sets=(put("subscription", "plan", "requested"),),
            weight=0.2, repeat=2, dwell_hours=(5 * DAY, 90 * DAY),
            violation="plan change requested on a line that is not active",
        ),
        EventSpec(
            "plan.changed", (PC,),
            requires=(need("subscription", "plan", "requested"),),
            sets=(put("subscription", "plan", "changed"),),
            weight=0.85, repeat=2, outcome="plan_decision", dwell_hours=(0.05, 24.0),
            violation="plan changed without a request",
        ),
        EventSpec(
            "plan.change_declined", (PC,),
            requires=(need("subscription", "plan", "requested"),),
            sets=(put("subscription", "plan", "declined"),),
            weight=0.15, repeat=2, outcome="plan_decision", dwell_hours=(0.05, 24.0),
            violation="plan change declined without a request",
        ),
        EventSpec(
            "fault.reported", (FM,),
            requires=(need("subscription", "service", "active"), need("fault", "fault", None, "resolved")),
            sets=(put("fault", "fault", "open"),),
            weight=0.15, repeat=2, dwell_hours=(7 * DAY, 120 * DAY),
            violation="fault reported on a line that is not active, or while another fault is open",
        ),
        EventSpec(
            "fault.diagnosed", (FM,),
            requires=(need("fault", "fault", "open"),),
            sets=(put("fault", "fault", "diagnosed"),),
            repeat=2, dwell_hours=(0.1, 8.0),
            violation="fault diagnosed before it was reported",
        ),
        EventSpec(
            "fault.resolved_remotely", (FM,),
            requires=(need("fault", "fault", "diagnosed"),),
            sets=(put("fault", "fault", "resolved"),),
            weight=0.7, repeat=2, outcome="fault_path", dwell_hours=(0.2, 24.0),
            violation="fault resolved before it was diagnosed",
        ),
        EventSpec(
            "engineer.dispatched", (FM,),
            requires=(need("fault", "fault", "diagnosed"),),
            sets=(put("fault", "fault", "visit"),),
            weight=0.3, repeat=2, outcome="fault_path", dwell_hours=(DAY, 5 * DAY),
            violation="engineer sent before the fault was diagnosed",
        ),
        EventSpec(
            "fault.fixed_on_site", (FM,),
            requires=(need("fault", "fault", "visit"),),
            sets=(put("fault", "fault", "resolved"),),
            repeat=2, dwell_hours=(1.0, 8.0),
            violation="fault fixed on site without an engineer visit",
        ),
        EventSpec(
            "cancellation.requested", (RE,),
            requires=(need("subscription", "service", "active"), need("subscription", "retention", None)),
            sets=(put("subscription", "retention", "requested"),),
            weight=0.08, dwell_hours=(30 * DAY, 300 * DAY),
            violation="cancellation requested on a line that is not active",
        ),
        EventSpec(
            "retention.offer_accepted", (RE,),
            requires=(need("subscription", "retention", "requested"),),
            sets=(put("subscription", "retention", "retained"),),
            weight=0.45, outcome="retention_outcome", dwell_hours=(0.05, 48.0),
            violation="retention offer accepted without a cancellation request",
        ),
        EventSpec(
            "subscription.cancelled", (RE,),
            requires=(need("subscription", "retention", "requested"),),
            sets=(put("subscription", "service", "cancelled"),),
            weight=0.4, outcome="retention_outcome", ends_journey=True, dwell_hours=(DAY, 30 * DAY),
            violation="subscription cancelled without a cancellation request",
        ),
        EventSpec(
            "port.out_completed", (RE,),
            requires=(need("subscription", "retention", "requested"),),
            sets=(put("subscription", "service", "ported_out"),),
            weight=0.15, outcome="retention_outcome", ends_journey=True, dwell_hours=(DAY, 3 * DAY),
            violation="number ported out without a cancellation request",
        ),
        EventSpec(
            "complaint.received", (CO,),
            # Complaints come from customers whose line is live, not from someone mid-order.
            requires=(need("party", "relationship", "customer"), need("complaint", "complaint", None)),
            sets=(put("complaint", "complaint", "open"),),
            weight=0.06, dwell_hours=(DAY, 60 * DAY),
            violation="complaint received twice or before the customer's line was live",
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
        SO: ("order.approved", "order.abandoned", "credit_check.failed"),
        AP: ("service.activated",),
        BP: ("bill.paid", "bill.overdue"),
        PC: ("plan.changed", "plan.change_declined"),
        FM: ("fault.resolved_remotely", "fault.fixed_on_site"),
        RE: ("retention.offer_accepted", "subscription.cancelled", "port.out_completed"),
        CO: ("complaint.received",),
    },
)

OBJECTS = {
    "party": ("P", "party", "individual"),
    "offering": ("F", "product_offering", "mobile_postpaid"),
    "order": ("O", "product_order", "new_connection"),
    "port": ("N", "port_request", "port_in"),
    "subscription": ("S", "subscription", "mobile_postpaid"),
    "fault": ("T", "trouble_ticket", "service_fault"),
    "complaint": ("M", "complaint", "service"),
}

ROLES = {
    "product.viewed": (("party", "prospect"), ("offering", "offering")),
    "order.started": (("party", "customer"), ("order", "order")),
    "order.submitted": (("party", "customer"), ("order", "order")),
    "order.abandoned": (("party", "customer"), ("order", "order")),
    "credit_check.started": (("party", "applicant"), ("order", "order")),
    "credit_check.passed": (("party", "applicant"), ("order", "order")),
    "credit_check.deposit_required": (("party", "applicant"), ("order", "order")),
    "credit_check.failed": (("party", "applicant"), ("order", "order")),
    "order.approved": (("party", "customer"), ("order", "order")),
    "sim.dispatched": (("party", "customer"), ("order", "order"), ("subscription", "subscription")),
    "port.requested": (("party", "customer"), ("port", "port"), ("order", "order")),
    "port.completed": (("party", "customer"), ("port", "port"), ("subscription", "subscription")),
    "port.failed": (("party", "customer"), ("port", "port")),
    "service.activated": (("party", "subscriber"), ("subscription", "subscription")),
    "bill.issued": (("party", "subscriber"), ("subscription", "subscription")),
    "bill.paid": (("party", "subscriber"), ("subscription", "subscription")),
    "bill.overdue": (("party", "subscriber"), ("subscription", "subscription")),
    "service.suspended": (("party", "subscriber"), ("subscription", "subscription")),
    "service.restored": (("party", "subscriber"), ("subscription", "subscription")),
    "plan.change_requested": (("party", "subscriber"), ("subscription", "subscription")),
    "plan.changed": (("party", "subscriber"), ("subscription", "subscription")),
    "plan.change_declined": (("party", "subscriber"), ("subscription", "subscription")),
    "fault.reported": (("party", "subscriber"), ("fault", "ticket"), ("subscription", "subscription")),
    "fault.diagnosed": (("fault", "ticket"), ("subscription", "subscription")),
    "fault.resolved_remotely": (("fault", "ticket"), ("subscription", "subscription")),
    "engineer.dispatched": (("fault", "ticket"), ("subscription", "subscription")),
    "fault.fixed_on_site": (("fault", "ticket"), ("subscription", "subscription")),
    "cancellation.requested": (("party", "subscriber"), ("subscription", "subscription")),
    "retention.offer_accepted": (("party", "subscriber"), ("subscription", "subscription")),
    "subscription.cancelled": (("party", "subscriber"), ("subscription", "subscription")),
    "port.out_completed": (("party", "subscriber"), ("subscription", "subscription")),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
    "complaint.resolved": (("party", "complainant"), ("complaint", "case")),
    "complaint.escalated": (("party", "complainant"), ("complaint", "case")),
}

EN = {
    "product.viewed": "The prospect viewed a mobile plan.",
    "order.started": "An order was started.",
    "order.submitted": "The order was submitted.",
    "order.abandoned": "The customer left the order unfinished.",
    "credit_check.started": "A credit check started.",
    "credit_check.passed": "The credit check passed.",
    "credit_check.deposit_required": "The credit check asked for a deposit.",
    "credit_check.failed": "The credit check failed.",
    "order.approved": "The order was approved.",
    "sim.dispatched": "The SIM was dispatched.",
    "port.requested": "A number port was requested.",
    "port.completed": "The number was ported in.",
    "port.failed": "The number port failed.",
    "service.activated": "The service was activated.",
    "bill.issued": "A bill was issued.",
    "bill.paid": "The bill was paid.",
    "bill.overdue": "The bill went overdue.",
    "service.suspended": "The service was suspended for non-payment.",
    "service.restored": "The service was restored.",
    "plan.change_requested": "A plan change was requested.",
    "plan.changed": "The plan was changed.",
    "plan.change_declined": "The plan change was declined.",
    "fault.reported": "A fault was reported.",
    "fault.diagnosed": "The fault was diagnosed.",
    "fault.resolved_remotely": "The fault was fixed remotely.",
    "engineer.dispatched": "An engineer was sent out.",
    "fault.fixed_on_site": "The engineer fixed the fault on site.",
    "cancellation.requested": "The customer asked to cancel.",
    "retention.offer_accepted": "The customer accepted a retention offer.",
    "subscription.cancelled": "The subscription was cancelled.",
    "port.out_completed": "The number was ported out to another provider.",
    "complaint.received": "A complaint was received.",
    "complaint.resolved": "The complaint was resolved.",
    "complaint.escalated": "The complaint went to the dispute resolution scheme.",
}
TR = {
    "product.viewed": "Aday bir mobil tarifeyi inceledi.",
    "order.started": "Sipariş başlatıldı.",
    "order.submitted": "Sipariş gönderildi.",
    "order.abandoned": "Müşteri siparişi yarım bıraktı.",
    "credit_check.started": "Kredi kontrolü başladı.",
    "credit_check.passed": "Kredi kontrolü olumlu sonuçlandı.",
    "credit_check.deposit_required": "Kredi kontrolü sonucunda depozito istendi.",
    "credit_check.failed": "Kredi kontrolü olumsuz sonuçlandı.",
    "order.approved": "Sipariş onaylandı.",
    "sim.dispatched": "SIM kart gönderildi.",
    "port.requested": "Numara taşıma talep edildi.",
    "port.completed": "Numara taşındı.",
    "port.failed": "Numara taşıma başarısız oldu.",
    "service.activated": "Hizmet etkinleştirildi.",
    "bill.issued": "Fatura kesildi.",
    "bill.paid": "Fatura ödendi.",
    "bill.overdue": "Faturanın ödeme süresi geçti.",
    "service.suspended": "Hizmet ödeme yapılmadığı için askıya alındı.",
    "service.restored": "Hizmet yeniden açıldı.",
    "plan.change_requested": "Tarife değişikliği talep edildi.",
    "plan.changed": "Tarife değiştirildi.",
    "plan.change_declined": "Tarife değişikliği reddedildi.",
    "fault.reported": "Arıza bildirildi.",
    "fault.diagnosed": "Arızanın nedeni belirlendi.",
    "fault.resolved_remotely": "Arıza uzaktan giderildi.",
    "engineer.dispatched": "Teknisyen yönlendirildi.",
    "fault.fixed_on_site": "Teknisyen arızayı yerinde giderdi.",
    "cancellation.requested": "Müşteri iptal talep etti.",
    "retention.offer_accepted": "Müşteri elde tutma teklifini kabul etti.",
    "subscription.cancelled": "Abonelik iptal edildi.",
    "port.out_completed": "Numara başka bir operatöre taşındı.",
    "complaint.received": "Şikayet kaydı açıldı.",
    "complaint.resolved": "Şikayet çözüldü.",
    "complaint.escalated": "Şikayet uyuşmazlık çözüm mercine taşındı.",
}

# TM Forum Open API domains and the action terms the episode builder uses for every pack.
OPERATIONS = {
    "product.viewed": ("Product Catalog", "Retrieve"),
    "order.started": ("Product Ordering", "Initiate"),
    "order.submitted": ("Product Ordering", "Update"),
    "order.abandoned": ("Product Ordering", "Control"),
    "credit_check.started": ("Credit Management", "Initiate"),
    "credit_check.passed": ("Credit Management", "Evaluate"),
    "credit_check.deposit_required": ("Credit Management", "Evaluate"),
    "credit_check.failed": ("Credit Management", "Evaluate"),
    "order.approved": ("Product Ordering", "Execute"),
    "sim.dispatched": ("Shipping Order", "Execute"),
    "port.requested": ("Number Portability", "Initiate"),
    "port.completed": ("Number Portability", "Execute"),
    "port.failed": ("Number Portability", "Execute"),
    "service.activated": ("Service Activation", "Execute"),
    "bill.issued": ("Customer Bill", "Initiate"),
    "bill.paid": ("Payment", "Execute"),
    "bill.overdue": ("Customer Bill", "Update"),
    "service.suspended": ("Service Activation", "Control"),
    "service.restored": ("Service Activation", "Update"),
    "plan.change_requested": ("Product Ordering", "Request"),
    "plan.changed": ("Product Inventory", "Update"),
    "plan.change_declined": ("Product Inventory", "Update"),
    "fault.reported": ("Trouble Ticket", "Initiate"),
    "fault.diagnosed": ("Service Problem", "Evaluate"),
    "fault.resolved_remotely": ("Trouble Ticket", "Execute"),
    "engineer.dispatched": ("Appointment", "Initiate"),
    "fault.fixed_on_site": ("Work Order", "Execute"),
    "cancellation.requested": ("Customer Retention", "Request"),
    "retention.offer_accepted": ("Customer Retention", "Execute"),
    "subscription.cancelled": ("Product Inventory", "Control"),
    "port.out_completed": ("Number Portability", "Control"),
    "complaint.received": ("Party Interaction", "Initiate"),
    "complaint.resolved": ("Party Interaction", "Execute"),
    "complaint.escalated": ("Party Interaction", "Execute"),
}

TRAJECTORY_TYPES = (
    "credit_declined",
    "order_abandoned",
    "churned",
    "retained",
    "payment_arrears",
    "fault_repair",
    "complaint_case",
    "plan_change",
    "number_port",
    "new_connection",
)


def classify(types: list[str]) -> str:
    present = set(types)
    if "credit_check.failed" in present:
        return "credit_declined"
    if "order.abandoned" in present:
        return "order_abandoned"
    if present & {"subscription.cancelled", "port.out_completed"}:
        return "churned"
    if "retention.offer_accepted" in present:
        return "retained"
    if "bill.overdue" in present:
        return "payment_arrears"
    if "fault.reported" in present:
        return "fault_repair"
    if "complaint.received" in present:
        return "complaint_case"
    if "plan.change_requested" in present:
        return "plan_change"
    if "port.requested" in present:
        return "number_port"
    return "new_connection"


FAILED_OUTCOMES = frozenset(
    {"order.abandoned", "credit_check.failed", "port.failed", "plan.change_declined", "subscription.cancelled", "port.out_completed", "complaint.escalated"}
)


def success(types: list[str]) -> bool:
    if not types or FAILED_OUTCOMES & set(types):
        return False
    # The last bill is paid, and a suspended line is back on.
    payments = [name for name in types if name in {"bill.paid", "bill.overdue"}]
    if payments and payments[-1] != "bill.paid":
        return False
    service = [name for name in types if name in {"service.suspended", "service.restored"}]
    return not service or service[-1] == "service.restored"


def intent(types: list[str]) -> str | None:
    if "port.requested" in types:
        return "number_port"
    if "order.started" in types:
        return "new_connection"
    return None


PROMPTS = {
    "en": (
        "Simulate a synthetic telecommunications customer journey. Do not invent real people, phone numbers, or account numbers.",
        "Narrate a synthetic mobile customer journey step by step. Keep every number and reference synthetic.",
        "Walk through a simulated telecom case from the first order onward. Use no real names or numbers.",
    ),
    "tr": (
        "Sentetik bir telekomünikasyon müşteri yolculuğu üret. Gerçek kişi, telefon numarası veya abone numarası uydurma.",
        "Sentetik bir mobil hat müşteri yolculuğunu adım adım anlat. Tüm numaralar ve referanslar sentetik olsun.",
        "İlk siparişten başlayarak simüle edilmiş bir telekom vakasını anlat. Gerçek isim ya da numara kullanma.",
    ),
}

OPENINGS = {
    "en": {
        "*": ("I want a new mobile plan.", "Can I get a SIM-only deal?", "I would like to sign up for a pay-monthly plan."),
        "number_port": ("I want to switch to you and keep my number.", "Can I bring my number over from another network?"),
    },
    "tr": {
        "*": ("Yeni bir mobil tarife istiyorum.", "Cihazsız bir tarife alabilir miyim?", "Faturalı bir hat almak istiyorum."),
        "number_port": ("Numaramı koruyarak size geçmek istiyorum.", "Numaramı başka bir operatörden taşıyabilir miyim?"),
    },
}

FOLLOW_UPS = {
    "en": ("What happened next?", "Go on.", "And then?", "What did the provider do after that?", "Continue, please."),
    "tr": ("Sonra ne oldu?", "Devam edin.", "Ardından?", "Operatör bundan sonra ne yaptı?", "Lütfen devam edin."),
}


def subtype(kind: str, default: str, steering: Any) -> str:
    if kind in {"offering", "subscription"} and steering.products:
        return steering.products[0]
    return default


PACK = PackSpec(
    sector="telecom",
    generator_id=GENERATOR_ID,
    pack_version=PACK_VERSION,
    lifecycle=LIFECYCLE,
    default_domain=SO,
    languages=("en", "tr"),
    objects=OBJECTS,
    roles=ROLES,
    phrases={"en": EN, "tr": TR},
    prompts=PROMPTS,
    openings=OPENINGS,
    follow_ups=FOLLOW_UPS,
    relationships=(
        ("party", "PLACED", "order"),
        ("order", "RESULTED_IN", "subscription"),
        ("party", "HOLDS", "subscription"),
        ("port", "BRINGS_NUMBER_TO", "subscription"),
        ("fault", "AFFECTS", "subscription"),
        ("party", "RAISED", "complaint"),
    ),
    system_events=frozenset(
        {
            "credit_check.started",
            "credit_check.passed",
            "credit_check.deposit_required",
            "credit_check.failed",
            "order.approved",
            "sim.dispatched",
            "port.completed",
            "port.failed",
            "service.activated",
            "bill.issued",
            "bill.overdue",
            "service.suspended",
            "service.restored",
            "plan.changed",
            "plan.change_declined",
            "fault.diagnosed",
            "fault.resolved_remotely",
            "subscription.cancelled",
            "port.out_completed",
            "complaint.resolved",
            "complaint.escalated",
        }
    ),
    fixed_channels={"engineer.dispatched": "field", "fault.fixed_on_site": "field"},
    default_channels={
        "product.viewed": "web",
        "order.started": "web",
        "order.submitted": "web",
        "order.abandoned": "web",
        "port.requested": "web",
        "bill.paid": "app",
        "plan.change_requested": "app",
        "fault.reported": "call_centre",
        "cancellation.requested": "call_centre",
        "retention.offer_accepted": "call_centre",
        "complaint.received": "call_centre",
    },
    amounts={
        "bill.paid": Amount(8.0, 140.0, "debit", "bill_payment"),
        "credit_check.deposit_required": Amount(50.0, 300.0, "debit", "security_deposit"),
        "retention.offer_accepted": Amount(5.0, 60.0, "credit", "retention_credit"),
    },
    qualifiers={
        ("sim.dispatched", "order"): "fulfilled_order",
        ("port.completed", "subscription"): "receiving_subscription",
        ("bill.paid", "subscription"): "billed_subscription",
        ("fault.reported", "subscription"): "affected_subscription",
    },
    effective_lag_hours={
        # A payment posts to the account later; a retention credit lands on the next bill.
        "bill.paid": (0.1, 48.0),
        "retention.offer_accepted": (DAY, 30 * DAY),
        "subscription.cancelled": (DAY, 30 * DAY),
    },
    trajectory_types=TRAJECTORY_TYPES,
    classify=classify,
    success=success,
    subtype=subtype,
    correctness_drops=("complaint.escalated",),
    intent=intent,
    goal={
        "en": "The journey ends without a failed outcome: no abandoned order, failed credit check, failed number port, declined plan change, cancellation or port-out, or escalated complaint; the last bill is paid and a suspended line is back on.",
        "tr": "Yolculuk başarısız bir sonuç olmadan biter: yarım bırakılan sipariş, olumsuz kredi kontrolü, başarısız numara taşıma, reddedilen tarife değişikliği, iptal veya numara taşıyarak ayrılma ya da üst mercie taşınan şikâyet olmaz; son fatura ödenir ve askıya alınan hat yeniden açılır.",
    },
    operations=OPERATIONS,
    agent={
        "en": {
            "system": "You are an operations agent at a telecommunications provider. Use only the listed operations and only the case's own identifiers.",
            "task": "You operate the provider's systems for customer {party}. {situation} Decide the next step and record it with one operation call, then say what you did.",
        },
        "tr": {
            "system": "Bir telekomünikasyon operatöründe operasyon temsilcisisiniz. Yalnızca listelenen işlemleri ve yalnızca bu vakanın kimliklerini kullanın.",
            "task": "{party} numaralı müşteri için operatörün sistemlerini yönetiyorsunuz. {situation} Sıradaki adıma karar verin, tek bir işlem çağrısıyla kaydedin ve ne yaptığınızı söyleyin.",
        },
    },
)
