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

Money carries `amount`, `currency`, `direction` (`debit` or `credit`, seen from the customer's account), and `amount_role`, such as `purchase` or `repayment`. An event-object link can carry a `qualifier` beyond the role, such as the account a purchase debits. `effective_time` differs from `event_time` where the domain says so: a card purchase posts to the account 12 to 72 hours after authorisation, and cover starts on the policy's start date. `recorded_at` trails the event by seconds.

`observation_status` is one of `observed`, `derived`, `imputed`, or `simulated`. An alternative branch is a simulated alternative. It is not labeled a causal counterfactual.

## Training layer

MiMo-V2.6 (LLM-Core Xiaomi, 2026) organizes an agent rollout as Sample, Sequence, Context, and Segment. This contract follows that hierarchy:

- A **sample** is one prompt and a group of sequences.
- A **sequence** is one rollout. Reward, advantage, and mask are nullable.
- A **context** is one dialogue branch.
- A **segment** is one turn. Only segments with role `assistant` are trainable.

Every sample names the trajectory it narrates in `trajectory_id`, and each sequence names its own.

## Groups and rewards

`group_size` (1 to 16) sets how many sequences a sample holds. A group's sequences share the journey up to its first real decision, then each continues with a fresh walk from that state, so one prompt yields different outcomes. The sequences also keep the first one's intent, such as a loan, so one opening fits them all. The first sequence is the group's primary trajectory; the others are alternatives of it that share its `group_id` and record `causal_claim: false`. The studio cap counts sequences, so 64 journeys hold 16 groups of 4. With a group size of 1, the primary and its one simulated alternative each get a sample of one.

Rewards come from `packages/sectors/src/sectors/rewards.py`, shared by every pack and following MiMo-V2.6:

- The verification term R_test is 1 when the journey reaches its goal legally. S_sol is 0.5 plus half the share of selected sub-domains the journey touches. S_beh is 1, because every generated journey replays legally through the pack's machines; the judge replaces both rubric terms in Slice 4.
- `binary_outcome` uses R_test. `groupwise_reward_synthesis` uses R = R_test × S_sol × S_beh. Advantages are the reward minus the group mean.
- `groupwise_advantage_redistribution` gives each passing sequence a quality factor, its S_sol × S_beh over the best in the group, rescales the passing advantages so their sum is conserved (the factor is capped at 2), and re-centres the group.
- `group_relative_length_penalty` discounts passing sequences longer than the median passing length, by up to 0.5 when a sequence is twice as long, and only when more than half the group passes.
- `segment_penalty` applies MiMo's segment-level penalty across the run, masking flagged turns in positive sequences and weighting them in negative ones while conserving each sign's total.
- Penalty rules (`empty_turn`, `repeated_turn`, `overlong_turn`) flag turns in `flagged_reason`. They start in record-only mode: flagged and counted, but no reward, mask, or advantage changes until a rule is switched to a masking strategy.
- The cascade drops a context with no surviving trainable turn, zeroes a sequence with no surviving context, and rejects a sample with no surviving sequence. `group_accepted` is false for all-pass and all-fail groups, as MiMo's dynamic sampler filters them, and empty for a group of one, which carries no group-relative signal.

`generation.rewards` summarises the mechanism, the groups, how many carry a signal and how many are accepted, the pass rate, and the flag counts. Assistant turns hold whole sentences, and user turns sit between them. Prompts and user lines come from a bank per sector and language. Reviewer notes appear only in the system segment.

Each sector pack names the event types and state dimensions that its hard checks enforce. The run records stay the same when a sector is added.

## How a pack generates

Each sector pack declares its events as transitions on orthogonal state machines, one dimension per object kind: relationship on the party, application and KYC for banking, underwriting and policy for insurance, and so on. Each event names the states it requires, the states it sets, how often it may repeat, and a hand-set prior weight. The shared engine in `packages/sectors/src/sectors/lifecycle.py` walks only legal transitions and samples a log-normal dwell time for each step, so an impossible journey cannot be generated. The same machines drive the hard checks: every trajectory is replayed through them in time order, so each rule the sampler obeys is verified again on the output.

- `banking-semi-markov-v2` covers onboarding and KYC, KYC review, deposits, cards, consumer credit with repayment, delinquency and cure, servicing, account closure, and complaints with resolution.
- `insurance-semi-markov-v2` covers quoting, underwriting with referral, binding and issue, premiums, claims with assessment and a decision, renewal, cancellation, and complaints with resolution.

The selected sub-domains decide which events are in scope. Events needed to reach them, such as the application before a card, are added automatically. A journey may end once it is long enough and has reached a milestone of a selected sub-domain; a declined application, a declined risk, a cancelled policy, or a closed account ends it at once. Length is met by sampling, never by repeating events.

A branch point is a step where the machines offered more than one legal choice. An alternative takes a different choice there, preferring the other outcome of the same decision, and continues with a fresh walk. Its `probability` is the sampler's probability of that choice at the branch point, and `causal_claim` is `false`: a simulated alternative, not a causal counterfactual.

Warm-start text weights the sampler instead of forcing events in: a named event in scope becomes more likely where it is legal, so a corpus naming both outcomes of a decision yields journeys of both kinds and none holding both. Currency, channel, and product terms still come from that sector's vocabulary. Cold start ignores the corpus. A drop note removes an event, and anything that could only follow it becomes unreachable. A keep note brings an event into scope and a journey does not end before it happens when it can. A revise note delays the event by at least a week. Reviewer notes appear only in the system segment, never in trainable text. The studio stores at most 64 primary trajectories for a run, and fewer when the event budget is exhausted.

## Run metadata

`generation` records the generator id, the pack version, the limit that stopped the run if any, and two reports:

- `steering`: the currency, channel, products, and events the warm-start text named, the events a negation cancelled, the events weighted because they are in scope, and one entry per document saying whether it was readable, why not, and what it contributed. Terms match on whole words; currency codes match only in capitals.
- `quality`: version 1 of the quality report. `complete` counts hard-check violations and filler runs and checks referential integrity; `comprehensive` counts distinct sequences, event-type coverage per selected sub-domain, the share of rare paths, and distinct transitions. `representative` and `qualitative` say they are not measured yet, and a cold start is marked unreferenced.

Runs are written in the languages the pack declares, English and Turkish today. The API refuses any other language instead of producing English.

## Export

`GET /runs/{run_id}/export/{part}` returns one part of an owned run, or 409 when the run has no generated candidate:

- `samples.jsonl`: one line per sample, with its group of sequences, turns, rewards, advantages, and `split`.
- `domain.jsonl`: one line per domain record, tagged with `record_type`; trajectory lines carry their sample's `split`.
- `ocel.json`: the domain layer in OCEL 2.0 JSON. Objects carry their state changes as time-stamped attributes and their relationships by predicate; events carry channel, money, and effective time, and link objects by qualifier or role.
- `manifest.json`: the configuration without the credential, counts, the split, judge cycles, the reward summary, the quality report, the steering report, a data card with scope, intended use, reference, jurisdiction, and known limitations, and a SHA-256 checksum for each other part. No secret, ciphertext, or fingerprint reaches it.

The split is assigned from `sha256(run_id|sample_id)`: train below 0.8, validation below 0.9, test above, so a re-export reproduces it. `?held_out=<sub_domain>` moves every sample whose sequences reach a milestone of that sub-domain, such as `loan.disbursed` for consumer credit, into a `heldout` split, to measure generalization rather than fit.

## Large runs

A run of more than 64 sequences is generated in batches of 256 sequences. Each batch has its own seed, derived from the run's, and prefixes every record id with its batch, such as `B0003.E00012`, so batches never collide and any id names its batch. Every batch passes the hard checks before it is written. The quality report and the overview (variants and the process map in `generation.overview`) are accumulated batch by batch, and `generation.storage` records the number of batches. A resumed run reproduces the same batches as an uninterrupted one.

`GET /runs/{run_id}/journeys` lists primary journeys a page at a time (`offset`, `limit` up to 500, and an optional `variant` id from the overview). `GET /runs/{run_id}/journeys/{trajectory_id}` returns one journey as a self-contained bundle: the trajectory, its alternatives or rollouts, and the events, objects, links, state changes, and samples they reference. Both work for small runs too. Notes on a large run are validated against its files, and a re-run resolves each note's target to its event or trajectory type instead of loading the parent.
