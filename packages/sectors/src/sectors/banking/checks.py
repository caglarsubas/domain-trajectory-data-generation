from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle


def banking_hard_checks(bundle: TrajectoryBundle) -> list[str]:
    """Structural and lifecycle errors. Empty means the bundle may be judged."""

    errors: list[str] = []
    object_ids = {obj.object_id for obj in bundle.objects}
    event_ids = {event.event_id for event in bundle.events}
    events_by_id = {event.event_id: event for event in bundle.events}

    if len(event_ids) != len(bundle.events):
        errors.append("duplicate event_id")
    if len(object_ids) != len(bundle.objects):
        errors.append("duplicate object_id")

    for link in bundle.event_objects:
        if link.event_id not in event_ids:
            errors.append(f"event_object references missing event {link.event_id}")
        if link.object_id not in object_ids:
            errors.append(f"event_object references missing object {link.object_id}")

    for rel in bundle.relationships:
        if rel.subject_id not in object_ids:
            errors.append(f"relationship {rel.relationship_id} missing subject {rel.subject_id}")
        if rel.object_id not in object_ids:
            errors.append(f"relationship {rel.relationship_id} missing object {rel.object_id}")

    for change in bundle.state_transitions:
        if change.event_id not in event_ids:
            errors.append(f"state transition references missing event {change.event_id}")
        if change.object_id not in object_ids:
            errors.append(f"state transition references missing object {change.object_id}")

    trajectory_ids = {item.trajectory_id for item in bundle.trajectories}
    for traj in bundle.trajectories:
        if traj.parent_trajectory_id and traj.parent_trajectory_id not in trajectory_ids:
            errors.append(f"trajectory {traj.trajectory_id} missing parent {traj.parent_trajectory_id}")
        if traj.branch_event_id and traj.branch_event_id not in event_ids:
            errors.append(f"trajectory {traj.trajectory_id} missing branch event {traj.branch_event_id}")
        ordered: list[tuple] = []
        for event_id in traj.event_ids:
            event = events_by_id.get(event_id)
            if event is None:
                errors.append(f"trajectory {traj.trajectory_id} references missing event {event_id}")
                continue
            ordered.append((event.event_time, event))
        ordered.sort(key=lambda pair: pair[0])
        errors.extend(_lifecycle(traj.trajectory_id, [event for _, event in ordered], bundle))

    for sample in bundle.samples:
        for sequence in sample.sequences:
            for context in sequence.contexts:
                for segment in context.segments:
                    if segment.trainable and segment.role != "assistant":
                        errors.append(f"trainable segment {segment.segment_id} is not an assistant turn")
    return errors


def _lifecycle(trajectory_id: str, events: list, bundle: TrajectoryBundle) -> list[str]:
    errors: list[str] = []
    issued: set[str] = set()
    approved = False

    objects_for: dict[str, set[str]] = {}
    for link in bundle.event_objects:
        objects_for.setdefault(link.event_id, set()).add(link.object_id)

    for event in events:
        linked = objects_for.get(event.event_id, set())
        if event.event_type == "application.approved":
            approved = True
        if event.event_type == "loan.disbursed" and not approved:
            errors.append(f"{trajectory_id}: loan disbursed before application approval")
        if event.event_type == "card.issued":
            issued.update(linked)
        if event.event_type == "card.activated" and (not linked or not linked.intersection(issued)):
            names = ", ".join(sorted(linked)) or "unknown-card"
            errors.append(f"{trajectory_id}: card activated before issuance ({names})")
    return errors
