# Trajectory contract

A released run is two linked layers. The domain layer is an object-centric event log. The training layer is the agent rollout hierarchy used for post-training.

## Domain layer

Records:

- `objects` — a version of a domain object (`object_id`, `object_type`, validity window, attributes, `pii_class`)
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

Each sector pack names the event types and state dimensions that its hard checks enforce. The run records stay the same when a sector is added.

## How a pack generates

Each sector pack declares its events as transitions on orthogonal state machines, one dimension per object kind: relationship on the party, application and KYC for banking, underwriting and policy for insurance, and so on. Each event names the states it requires, the states it sets, how often it may repeat, and a hand-set prior weight. The shared engine in `packages/sectors/src/sectors/lifecycle.py` walks only legal transitions and samples a log-normal dwell time for each step, so an impossible journey cannot be generated. The same machines drive the hard checks: every trajectory is replayed through them in time order, so each rule the sampler obeys is verified again on the output.

- `banking-semi-markov-v2` covers onboarding and KYC, KYC review, deposits, cards, consumer credit with repayment, delinquency and cure, servicing, account closure, and complaints with resolution.
- `insurance-semi-markov-v2` covers quoting, underwriting with referral, binding and issue, premiums, claims with assessment and a decision, renewal, cancellation, and complaints with resolution.

The selected sub-domains decide which events are in scope. Events needed to reach them, such as the application before a card, are added automatically. A journey may end once it is long enough and has reached a milestone of a selected sub-domain; a declined application, a declined risk, a cancelled policy, or a closed account ends it at once. Length is met by sampling, never by repeating events.

A branch point is a step where the machines offered more than one legal choice. An alternative takes a different choice there, preferring the other outcome of the same decision, and continues with a fresh walk. Its `probability` is the sampler's probability of that choice at the branch point, and `causal_claim` is `false`: a simulated alternative, not a causal counterfactual.

Warm-start text weights the sampler instead of forcing events in: a named event in scope becomes more likely where it is legal, so a corpus naming both outcomes of a decision yields journeys of both kinds and none holding both. Currency, channel, and product terms still come from that sector's vocabulary. Cold start ignores the corpus. A drop note removes an event, and anything that could only follow it becomes unreachable. A keep note brings an event into scope and a journey does not end before it happens when it can. A revise note delays the event by at least a week. Reviewer notes appear only in the system segment, never in trainable text. The studio stores at most 64 primary trajectories for a run, and fewer when the event budget is exhausted.
