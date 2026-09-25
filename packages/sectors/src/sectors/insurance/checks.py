from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle


def insurance_hard_checks(bundle: TrajectoryBundle) -> list[str]:
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
        ordered = []
        for event_id in traj.event_ids:
            event = events_by_id.get(event_id)
            if event is None:
                errors.append(f"trajectory {traj.trajectory_id} references missing event {event_id}")
                continue
            ordered.append((event.event_time, event))
        ordered.sort(key=lambda pair: pair[0])
        errors.extend(_lifecycle(traj.trajectory_id, [event for _, event in ordered]))

    for sample in bundle.samples:
        for sequence in sample.sequences:
            for context in sequence.contexts:
                for segment in context.segments:
                    if segment.trainable and segment.role != "assistant":
                        errors.append(f"trainable segment {segment.segment_id} is not an assistant turn")
    return errors


def _lifecycle(trajectory_id: str, events: list) -> list[str]:
    errors: list[str] = []
    accepted = False
    issued = False
    notified = False
    assessed = False
    for event in events:
        kind = event.event_type
        if kind == "underwriting.accepted":
            accepted = True
        if kind in {"policy.bound", "policy.issued"} and not accepted:
            errors.append(f"{trajectory_id}: policy issued before underwriting acceptance")
        if kind == "policy.issued":
            issued = True
        if kind == "premium.paid" and not issued:
            errors.append(f"{trajectory_id}: premium paid before policy issue")
        if kind in {"policy.renewed", "policy.cancelled"} and not issued:
            errors.append(f"{trajectory_id}: policy changed before issue")
        if kind == "claim.notified":
            if not issued:
                errors.append(f"{trajectory_id}: claim notified before policy issue")
            notified = True
        if kind == "claim.assessed":
            if not notified:
                errors.append(f"{trajectory_id}: claim assessed before notification")
            assessed = True
        if kind in {"claim.settled", "claim.denied"} and not assessed:
            errors.append(f"{trajectory_id}: claim decided before assessment")
    return errors
