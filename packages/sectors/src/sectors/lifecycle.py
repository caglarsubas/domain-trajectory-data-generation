"""State-machine lifecycle engine shared by every sector pack.

A pack declares its events as transitions on orthogonal state machines, one
dimension per object kind. The sampler walks only legal transitions, so an
impossible journey cannot be generated. The checker replays a trajectory
through the same machines, so every rule the sampler obeys is verified again
on the output.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

State = dict[tuple[str, str], str]

# Weight of ending the journey once it may end, scaled by how much of the selected scope it covers.
STOP_WEIGHT = 0.6
NAMED_EVENT_WEIGHT = 4.0
KEPT_EVENT_WEIGHT = 6.0


@dataclass(frozen=True)
class Guard:
    kind: str
    dimension: str
    states: tuple[str | None, ...]


@dataclass(frozen=True)
class Effect:
    kind: str
    dimension: str
    state: str


def need(kind: str, dimension: str, *states: str | None) -> Guard:
    return Guard(kind, dimension, tuple(states))


def put(kind: str, dimension: str, state: str) -> Effect:
    return Effect(kind, dimension, state)


@dataclass(frozen=True)
class EventSpec:
    event_type: str
    sub_domains: tuple[str, ...]
    requires: tuple[Guard, ...] = ()
    sets: tuple[Effect, ...] = ()
    weight: float = 1.0
    # A different weight while a guard holds, in place of `weight`; the first that holds wins. A choice can be likelier
    # in one state than in another, such as walking away from an application before or after documents are asked for.
    weight_when: tuple[tuple[Guard, float], ...] = ()
    repeat: int = 1
    ends_journey: bool = False
    # Events that share an outcome are the alternatives at a branch point, such as approved or declined.
    outcome: str | None = None
    dwell_hours: tuple[float, float] = (1.0, 24.0)
    # A recurring event, such as a monthly premium, falls due this long after its previous occurrence,
    # whatever happened in between; dwell_hours then times only the first occurrence.
    cycle_hours: tuple[float, float] | None = None
    violation: str | None = None
    # When an opening event is legal, a journey starts with one, such as a product view before a complaint.
    opening: bool = False
    # A journey does not end right after this event while one of these is legal, such as a first bill after a
    # line goes live. Only events in the run's scope are legal, so an out-of-scope follow-up never holds a journey.
    follow_up: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.cycle_hours is not None and not 0 < self.cycle_hours[0] <= self.cycle_hours[1]:
            raise ValueError(f"{self.event_type}: a cycle needs 0 < low <= high, not {self.cycle_hours}")


@dataclass(frozen=True)
class LifecycleSpec:
    events: tuple[EventSpec, ...]
    # Reaching one of these for a selected sub-domain lets a journey end.
    milestones: dict[str, tuple[str, ...]]
    object_types: dict[str, str]
    _index: dict[str, EventSpec] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {item.event_type: item for item in self.events})

    @property
    def namespace(self) -> tuple[str, ...]:
        return tuple(item.event_type for item in self.events)

    @property
    def sub_domains(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.events:
            for domain in item.sub_domains:
                if domain not in seen:
                    seen.append(domain)
        return tuple(seen)

    @property
    def dimensions(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.events:
            for effect in item.sets:
                if effect.dimension not in seen:
                    seen.append(effect.dimension)
        return tuple(seen)

    def kind_of(self, event_type: str) -> str:
        """The object kind an event belongs to: the first machine it moves, else the first it reads."""
        spec = self[event_type]
        if spec.sets:
            return spec.sets[0].kind
        if spec.requires:
            return spec.requires[0].kind
        return "party"

    def get(self, event_type: str) -> EventSpec | None:
        return self._index.get(event_type)

    def __getitem__(self, event_type: str) -> EventSpec:
        return self._index[event_type]


def satisfied(spec: EventSpec, state: State) -> bool:
    return all(state.get((guard.kind, guard.dimension)) in guard.states for guard in spec.requires)


def apply(spec: EventSpec, state: State) -> None:
    for effect in spec.sets:
        state[(effect.kind, effect.dimension)] = effect.state


def allowed_events(
    lifecycle: LifecycleSpec,
    sub_domains: list[str],
    blocked: set[str],
    extra: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Events of the selected sub-domains, plus `extra`, plus the events needed to reach them, in declared order."""
    selected = set(sub_domains)
    chosen = {item.event_type for item in lifecycle.events if selected & set(item.sub_domains)}
    chosen.update(name for name in extra if lifecycle.get(name) is not None)
    chosen -= blocked
    while True:
        reached, facts = _reachable(lifecycle, chosen)
        pending = [name for name in lifecycle.namespace if name in chosen and name not in reached]
        added = None
        for name in pending:
            guard = next(
                (item for item in lifecycle[name].requires if not _guard_reachable(item, facts)),
                None,
            )
            if guard is None:
                continue
            producers = [
                item.event_type
                for item in lifecycle.events
                if item.event_type not in blocked
                and item.event_type not in chosen
                and any(e.kind == guard.kind and e.dimension == guard.dimension and e.state in guard.states for e in item.sets)
            ]
            if producers:
                # Prefer the heaviest producer, so an enabler is the usual way in rather than a rare path.
                added = max(producers, key=lambda item: (lifecycle[item].weight, -lifecycle.namespace.index(item)))
                break
        if added is None:
            break
        chosen.add(added)
    return tuple(name for name in lifecycle.namespace if name in chosen)


def _guard_reachable(guard: Guard, facts: set[tuple[str, str, str]]) -> bool:
    return None in guard.states or any((guard.kind, guard.dimension, state) in facts for state in guard.states)


def _reachable(lifecycle: LifecycleSpec, chosen: set[str]) -> tuple[set[str], set[tuple[str, str, str]]]:
    """Events that could fire from the initial state, ignoring that later states replace earlier ones."""
    facts: set[tuple[str, str, str]] = set()
    reached: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name in lifecycle.namespace:
            if name not in chosen or name in reached:
                continue
            spec = lifecycle[name]
            if all(_guard_reachable(guard, facts) for guard in spec.requires):
                reached.add(name)
                facts.update((effect.kind, effect.dimension, effect.state) for effect in spec.sets)
                changed = True
    return reached, facts


@dataclass
class Step:
    event_type: str
    options: tuple[tuple[str, float], ...]
    state_before: State
    counts_before: dict[str, int]


@dataclass
class Path:
    steps: list[Step]
    # True when the walk chose to stop while events were still legal, which is itself a decision.
    stopped: bool = False
    # True when nothing in the scope was legal any more: the journey is complete for what the run selected.
    exhausted: bool = False
    state: State = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def types(self) -> list[str]:
        return [step.event_type for step in self.steps]


class Walker:
    def __init__(
        self,
        lifecycle: LifecycleSpec,
        *,
        allowed: tuple[str, ...],
        sub_domains: list[str],
        named: tuple[str, ...] = (),
        kept: tuple[str, ...] = (),
        calibration=None,
    ) -> None:
        self.lifecycle = lifecycle
        # Observed next-step shares from a data source reweight the legal choices; None keeps the priors.
        self.calibration = calibration if calibration is not None and not calibration.empty else None
        self.allowed = allowed
        self.kept = tuple(name for name in kept if name in allowed)
        self.weights: dict[str, float] = {}
        self.conditional: dict[str, tuple[tuple[tuple[str, str], frozenset, float], ...]] = {}
        for name in allowed:
            factor = 1.0
            if name in named:
                factor *= NAMED_EVENT_WEIGHT
            if name in self.kept:
                factor *= KEPT_EVENT_WEIGHT
            self.weights[name] = lifecycle[name].weight * factor
            # Weights that hold in some states only are scaled as the event's own weight is.
            if lifecycle[name].weight_when:
                self.conditional[name] = tuple(
                    ((guard.kind, guard.dimension), frozenset(guard.states), weight * factor) for guard, weight in lifecycle[name].weight_when
                )
        # Each allowed event's repeat limit, guards, and conditional weights, compiled once: options() runs for every
        # step of every walk.
        self._checks = tuple(
            (
                name,
                lifecycle[name].repeat,
                tuple(((guard.kind, guard.dimension), frozenset(guard.states)) for guard in lifecycle[name].requires),
                self.conditional.get(name, ()),
            )
            for name in allowed
        )
        # What each event sets and what its guards read, for asking which event a step can follow (leads), and for each
        # event the ones the data never contains that set something it reads, which a step can come through.
        self._sets = {name: {(effect.kind, effect.dimension): effect.state for effect in lifecycle[name].sets} for name in allowed}
        self._guards = {name: guards for name, _, guards, _ in self._checks}
        unseen = [] if self.calibration is None else [name for name in allowed if name not in self.calibration.observed_events]
        self._through = {
            name: tuple(middle for middle in unseen if middle != name and any(key in self._sets[middle] for key, _ in self._guards[name]))
            for name in allowed
        }
        self.goals: list[tuple[str, ...]] = []
        for domain in lifecycle.sub_domains:
            if domain not in sub_domains:
                continue
            reachable = tuple(name for name in lifecycle.milestones.get(domain, ()) if name in allowed)
            if reachable:
                self.goals.append(reachable)

    def reaches_goal(self, types: list[str]) -> bool:
        """Whether a path reached a milestone of a selected sub-domain, so it belongs to the run's scope."""
        seen = set(types)
        return not self.goals or any(seen & set(goal) for goal in self.goals)

    def options(self, state: State, counts: dict[str, int], *, first: bool = False) -> list[tuple[str, float]]:
        found = []
        weights = self.weights
        for name, repeat, guards, conditional in self._checks:
            if counts.get(name, 0) >= repeat:
                continue
            for key, states in guards:
                if state.get(key) not in states:
                    break
            else:
                weight = weights[name]
                for key, states, value in conditional:
                    if state.get(key) in states:
                        weight = value
                        break
                found.append((name, weight))
        if first:
            openings = [item for item in found if self.lifecycle[item[0]].opening]
            return openings or found
        return found

    def leads(self, state: State, counts: dict[str, int], option: str, event: str) -> bool:
        """Whether `event`, not legal now, is legal once `option` is taken in this state, right after it or after one
        more event the data never contains: documents, then a passed check the data does not record, then approval.

        An option that sets nothing the event's guards read leaves it as it is, so only the others are checked.
        """
        guards = self._guards.get(event)
        if guards is None or counts.get(event, 0) + (option == event) >= self.lifecycle[event].repeat:
            return False
        sets = self._sets[option]
        if any(key in sets for key, _ in guards) and all(sets.get(key, state.get(key)) in states for key, states in guards):
            return True
        for middle in self._through[event]:
            if middle == option or counts.get(middle, 0) >= self.lifecycle[middle].repeat:
                continue
            if all(sets.get(key, state.get(key)) in states for key, states in self._guards[middle]):
                after = {**sets, **self._sets[middle]}
                if all(after.get(key, state.get(key)) in states for key, states in guards):
                    return True
        return False

    def reweighted(self, options: list[tuple[str, float]], types: list[str], state: State, counts: dict[str, int]) -> list[tuple[str, float]]:
        """The options reweighted by the data's next-step shares after the journey so far."""
        previous, before = self.calibration.context(types)
        return self.calibration.reweight(
            previous, options, before, taken=counts, leads=lambda option, event: self.leads(state, counts, option, event)
        )

    def walk(
        self,
        rng: random.Random,
        *,
        floor: int,
        cap: int,
        state: State | None = None,
        counts: dict[str, int] | None = None,
        prefix: list[Step] | None = None,
        first: str | None = None,
    ) -> Path:
        state = dict(state or {})
        counts = dict(counts or {})
        steps = list(prefix or [])
        forced = first
        stopped = exhausted = False
        while len(steps) < cap:
            options = self.options(state, counts, first=not steps)
            if not options:
                exhausted = True
                break
            if self.calibration is not None:
                options = self.reweighted(options, [step.event_type for step in steps], state, counts)
            if forced is not None:
                choice = forced
                forced = None
            else:
                covered = sum(1 for goal in self.goals if any(counts.get(name) for name in goal))
                legal = {name for name, _ in options}
                may_end = (
                    len(steps) >= floor
                    and (not self.goals or covered > 0)
                    and all(counts.get(name) for name in self.kept)
                    and not (steps and legal & set(self.lifecycle[steps[-1].event_type].follow_up))
                )
                stop = STOP_WEIGHT * (covered / len(self.goals) if self.goals else 1.0) if may_end else 0.0
                total = stop + sum(weight for _, weight in options)
                pick = rng.random() * total
                if pick < stop:
                    stopped = True
                    break
                pick -= stop
                choice = options[-1][0]
                for name, weight in options:
                    if pick < weight:
                        choice = name
                        break
                    pick -= weight
            spec = self.lifecycle[choice]
            steps.append(Step(choice, tuple(options), dict(state), dict(counts)))
            apply(spec, state)
            counts[choice] = counts.get(choice, 0) + 1
            if spec.ends_journey:
                break
        return Path(steps, stopped=stopped, state=state, counts=counts, exhausted=exhausted)

    def branch(self, path: Path, rng: random.Random, *, cap: int) -> tuple[Path, int, float] | None:
        """A simulated alternative: a different legal choice at one step, then a fresh walk."""
        outcome_points: list[int] = []
        other_points: list[int] = []
        for index, step in enumerate(path.steps):
            if index == 0:
                continue
            rivals = [name for name, _ in step.options if name != step.event_type]
            if not rivals:
                continue
            group = self.lifecycle[step.event_type].outcome
            if group and any(self.lifecycle[name].outcome == group for name in rivals):
                outcome_points.append(index)
            else:
                other_points.append(index)
        points = outcome_points or other_points
        if not points:
            return None
        index = points[rng.randrange(len(points))]
        step = path.steps[index]
        group = self.lifecycle[step.event_type].outcome if outcome_points else None
        rivals = [
            (name, weight)
            for name, weight in step.options
            if name != step.event_type and (group is None or self.lifecycle[name].outcome == group)
        ]
        total = sum(weight for _, weight in rivals)
        pick = rng.random() * total
        alternative = rivals[-1][0]
        for name, weight in rivals:
            if pick < weight:
                alternative = name
                break
            pick -= weight
        probability = dict(step.options)[alternative] / sum(weight for _, weight in step.options)
        walked = self.walk(
            rng,
            floor=1,
            cap=cap,
            state=step.state_before,
            counts=step.counts_before,
            prefix=path.steps[:index],
            first=alternative,
        )
        return walked, index, probability


def dwell(rng: random.Random, low: float, high: float) -> float:
    """Hours to the next event: log-normal around the geometric middle of the range, clamped to it."""
    if high <= 0:
        return 0.0
    low = max(low, 1e-3)
    if high <= low:
        return low
    median = math.sqrt(low * high)
    sigma = math.log(high / low) / 3.3
    return min(high, max(low, rng.lognormvariate(math.log(median), sigma)))


def replay(lifecycle: LifecycleSpec, trajectory_id: str, events: list, links: dict[str, list[str]], object_types: dict[str, str]) -> list[str]:
    """Replay one trajectory, in time order, through the pack's machines."""
    errors: list[str] = []
    kinds = {object_type: kind for kind, object_type in lifecycle.object_types.items()}
    state: dict[tuple[str, str], str] = {}
    latest: dict[str, str] = {}
    for event in events:
        spec = lifecycle.get(event.event_type)
        if spec is None:
            errors.append(f"{trajectory_id}: unknown event type {event.event_type}")
            continue
        linked: dict[str, str] = {}
        for object_id in links.get(event.event_id, []):
            kind = kinds.get(object_types.get(object_id, ""))
            if kind is not None:
                linked[kind] = object_id
                latest[kind] = object_id

        def holder(kind: str) -> str:
            return linked.get(kind) or latest.get(kind) or f"<{kind}>"

        for guard in spec.requires:
            current = state.get((holder(guard.kind), guard.dimension))
            if current not in guard.states:
                reason = spec.violation or f"{event.event_type} while {guard.kind} {guard.dimension} is {current or 'unset'}"
                errors.append(f"{trajectory_id}: {reason} ({event.event_id})")
                break
        for effect in spec.sets:
            state[(holder(effect.kind), effect.dimension)] = effect.state
    return errors


def structural_checks(bundle) -> list[str]:
    """Identity, reference, and trainability errors shared by every pack."""
    errors: list[str] = []
    object_ids = {obj.object_id for obj in bundle.objects}
    event_ids = {event.event_id for event in bundle.events}
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
        for event_id in traj.event_ids:
            if event_id not in event_ids:
                errors.append(f"trajectory {traj.trajectory_id} references missing event {event_id}")
    for sample in bundle.samples:
        for sequence in sample.sequences:
            for context in sequence.contexts:
                for segment in context.segments:
                    if segment.trainable and segment.role != "assistant":
                        errors.append(f"trainable segment {segment.segment_id} is not an assistant turn")
    return errors


def lifecycle_checks(lifecycle: LifecycleSpec, bundle) -> list[str]:
    errors = structural_checks(bundle)
    events_by_id = {event.event_id: event for event in bundle.events}
    object_types = {obj.object_id: obj.object_type for obj in bundle.objects}
    links: dict[str, list[str]] = {}
    for link in bundle.event_objects:
        links.setdefault(link.event_id, []).append(link.object_id)
    for traj in bundle.trajectories:
        ordered = sorted(
            (events_by_id[event_id] for event_id in traj.event_ids if event_id in events_by_id),
            key=lambda event: event.event_time,
        )
        errors.extend(replay(lifecycle, traj.trajectory_id, ordered, links, object_types))
    return errors
