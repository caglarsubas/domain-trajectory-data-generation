# Trajectory contract

A released run is two linked layers. The domain layer is an object-centric event log. The training layer is the agent rollout hierarchy used for post-training.

## Domain layer

Records:

- `objects` — a version of a banking object (`object_id`, `object_type`, validity window, attributes, `pii_class`)
- `relationships` — a temporal link (`subject_id`, `predicate`, `object_id`, `valid_from`, `valid_to`)
- `events` — one business occurrence, with `event_time`, `effective_time`, `recorded_at`, and `timestamp_precision`
- `event_objects` — the role an object played in an event
- `state_transitions` — `state_before` and `state_after` on one dimension
- `trajectories` — a projection over events, including `parent_trajectory_id`, `branch_event_id`, `generator_id`, and `probability`

`observation_status` is one of `observed`, `derived`, `imputed`, or `simulated`. An alternative branch is a simulated alternative. It is not labeled a causal counterfactual.

## Training layer

MiMo-V2.6 (LLM-Core Xiaomi, 2026) organizes an agent rollout as Sample, Sequence, Context, and Segment. This contract follows that hierarchy:

- A **sample** is one prompt and a group of sequences.
- A **sequence** is one rollout. Reward, advantage, and mask are nullable.
- A **context** is one dialogue branch.
- A **segment** is one turn. Only segments with role `assistant` are trainable.

The banking sector pack names the event types and state dimensions that hard checks enforce. Other sectors will supply their own packs later without changing these records.

`banking-semi-markov-v1` fills these records for a banking run. Domain order is fixed, dwell times vary, and a branch is a simulated alternative rather than a causal counterfactual. The studio stores at most 64 primary trajectories for a run, and fewer when the event budget is exhausted. Warm-start text contributes only matched banking terms such as currency, channel, and product. Cold start ignores the corpus.
