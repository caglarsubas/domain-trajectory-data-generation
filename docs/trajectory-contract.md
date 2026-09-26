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

`group_size` (1 to 16) sets how many sequences a sample holds. A group's sequences share the journey up to its first real decision, then each continues with a fresh walk from that state, so one prompt yields different outcomes. A journey that offered no choice but stopped while it could go on decides at its end, so the others may carry on from there. A journey or sequence that the domain ends early, such as an abandoned application, is kept even when it is shorter than the minimum length, since dropping it would leave mostly the successes; this holds for the first sequence too, so it is drawn like the others and not chosen for success. The sequences also keep one intent, such as a loan, so one opening fits them all: the first sequence's, or, when it reached no product, such as a declined application, the first rollout's that did, and the opening asks for that product. The first sequence is the group's primary trajectory; the others are alternatives of it that share its `group_id` and record `causal_claim: false`. The studio cap counts sequences, so 64 journeys hold 16 groups of 4. With a group size of 1, the primary and its one simulated alternative each get a sample of one.

Rewards come from `packages/sectors/src/sectors/rewards.py`, shared by every pack and following MiMo-V2.6:

- The verification term R_test is 1 when the sequence passes the run's signal (see Signals and evaluation). Under `outcome`, the default, that is when the journey reaches its goal: in banking a journey misses it when the application is abandoned or declined, KYC fails, a complaint is not upheld, a limit change is declined, it ends on a declined card purchase, or its loan ends delinquent. S_sol is the solution rubric's score and S_beh the behavior rubric's, both checked by code over the journey's events and waits.
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

- `banking-semi-markov-v2` (pack `banking-pack-4`) covers onboarding and KYC with abandonment, KYC review, deposits, cards with authorised and declined purchases, consumer credit with repayment, delinquency and cure, servicing with limit changes that may be declined, account closure, and complaints resolved or not upheld. Deposits and consumer credit own their application, so its abandonment, a failed KYC, and a decline are in their scope.
- `insurance-semi-markov-v2` covers quoting, underwriting with referral, binding and issue, premiums, claims with assessment and a decision, renewal, cancellation, and complaints with resolution.
- `telecom-semi-markov-v1` (pack `telecom-pack-1`) covers ordering with abandonment and a credit check that passes, asks for a deposit, or fails; SIM dispatch and activation with a number port settled before the line goes live; monthly bills paid or overdue, suspension and restoration; plan changes that may be declined; faults fixed remotely or by an engineer; retention after a cancellation request (an offer accepted, a cancellation, or a port-out); and complaints resolved or escalated.

Each pack also carries `operations`, a map from event to (service domain, action) after its industry's API model (BIAN, insurance capability domains, TM Forum Open APIs), which names episode operations, and `agent`, the episode agent's system text and task per language. `sectors.gates.run_gates` checks a pack against the gates banking passes; only packs that pass are registered and served by `GET /sectors`, which adds each pack's `default_sub_domains`. A jurisdiction profile's `rules` give a sector its own rules in place of KYC in the system segment and the judge brief.

The selected sub-domains decide which events are in scope. Events needed to reach them, such as the application before a card, are added automatically. A journey may end once it is long enough and has reached a milestone of a selected sub-domain; an abandoned or declined application, a declined risk, a cancelled policy, or a closed account ends it at once. Length is met by sampling, never by repeating events.

A branch point is a step where the machines offered more than one legal choice. An alternative takes a different choice there, preferring the other outcome of the same decision, and continues with a fresh walk. Its `probability` is the sampler's probability of that choice at the branch point, and `causal_claim` is `false`: a simulated alternative, not a causal counterfactual.

Warm-start text weights the sampler instead of forcing events in: a named event in scope becomes more likely where it is legal, so a corpus naming both outcomes of a decision yields journeys of both kinds and none holding both. Currency, channel, and product terms still come from that sector's vocabulary. Cold start ignores the corpus. A drop note removes an event, and anything that could only follow it becomes unreachable. A keep note brings an event into scope and a journey does not end before it happens when it can. A revise note delays the event by at least a week. Reviewer notes appear only in the system segment, never in trainable text. The studio stores at most 64 primary trajectories for a run, and fewer when the event budget is exhausted.

## Run metadata

`generation` records the generator id, the pack version, the limit that stopped the run if any, `notes` when the run was generated from notes (each note and revision note with what it did), the `jurisdiction` profile, `calibration` when data sources calibrated it (sources, cases, observed steps, timed steps), and two reports:

- `steering`: the currency, channel, products, and events the study's steering facts named (explicit facts, and implied ones a person accepted), `facts` with how many steered, awaited review, were accepted or rejected, and what came from defaults, the events a negation cancelled, the events weighted because they are in scope, and one entry per document saying whether it was readable, why not, and what it contributed. Terms match on whole words; currency codes match only in capitals.
- `quality`: version 1 of the quality report. `complete` counts hard-check violations and filler runs and checks referential integrity; `comprehensive` counts distinct sequences, event-type coverage per selected sub-domain, the share of rare paths, and distinct transitions. `representative` is measured for a calibrated run: fitness, precision, and next-step divergence against the data among the events both share; otherwise it says how to measure it, and a cold start is marked unreferenced. `qualitative` says it is not measured yet.

Runs are written in the languages the pack declares, English and Turkish today. The API refuses any other language instead of producing English.

## Export

`GET /runs/{run_id}/export/{part}` returns one part of an owned run, or 409 when the run has no generated candidate:

- `samples.jsonl`: one line per sample, with its group of sequences, turns, rewards, advantages, and `split`.
- `prefixes.jsonl`: one line per trainable assistant turn, with the conversation before it (`prefix`), the turn (`target`), the sequence's reward, the turn's advantage, and the sample's `split`.
- `episodes.jsonl` and `episodes-<harness>.jsonl`: see Episodes.
- `decisions.jsonl` and `decision-record.schema.json`: see Decision records.
- `tasks.jsonl` and `evaluation.json`: see Signals and evaluation.
- `domain.jsonl`: one line per domain record, tagged with `record_type`; trajectory lines carry their sample's `split`.
- `ocel.json`: the domain layer in OCEL 2.0 JSON. Objects carry their state changes as time-stamped attributes and their relationships by predicate; events carry channel, money, and effective time, and link objects by qualifier or role.
- `manifest.json`: the configuration without the credential, counts, the split, the parts each consumer uses (`consumer_parts`, `parts_for_this_run`), judge cycles, the reward summary, the quality report, the steering report, a data card with scope, intended use, reference, jurisdiction, and known limitations, and a SHA-256 checksum for each other part. No secret, ciphertext, or fingerprint reaches it.

Export follows the judge: a run whose latest cycle the judge accepted exports as it is, and any other only with `allow_unaccepted=true`. The manifest's `review` records `accepted`, the `cycle`, and `exported_without_acceptance`, and `judge_cycles` carries each cycle's models, sampled journeys, per-model scores, agreement, flags, and control result.

The split is assigned from `sha256(run_id|sample_id)`: train below 0.8, validation below 0.9, test above, so a re-export reproduces it. `?held_out=<sub_domain>` moves every sample whose sequences reach a milestone of that sub-domain, such as `loan.disbursed` for consumer credit, into a `heldout` split, to measure generalization rather than fit.

A run above 64 sequences is exported by a job instead. `POST /runs/{run_id}/exports` with `{"held_out": <sub_domain or null>}` queues an `export` job, or returns the current one when that export is already queued, running, or ready; small runs get 409 and export directly. The job streams the run batch by batch into `DATA_DIR/runs/<run id>/exports/<all | heldout-<sub_domain>>/`, staged in a temporary directory and renamed when complete, so a half-written export is never served. Every part but the manifest is gzipped; the manifest's checksums are of the uncompressed content, and it adds `file_sizes` (uncompressed bytes) and the run's `target`. `GET /runs/{run_id}/exports` lists the latest export per held-out choice with `ready`, `sizes`, `download_sizes` (bytes on disk), and its job. Until an export is ready, `GET /runs/{run_id}/export/{part}` answers 409 for a large run; afterwards it serves the file, as `application/gzip` for the data parts. An export job never changes the run's own status or job.

## Large runs

A run of more than 64 sequences is generated in batches of 256 sequences. Each batch has its own seed, derived from the run's, and prefixes every record id with its batch, such as `B0003.E00012`, so batches never collide and any id names its batch. Every batch passes the hard checks before it is written. The quality report and the overview (variants and the process map in `generation.overview`, where each event type carries its typical `hours` from the journey's first event and its typical `step`, and each transition its typical `hours` of dwell, averaged on a log scale) are accumulated batch by batch, and `generation.storage` records the number of batches. A resumed run reproduces the same batches as an uninterrupted one.

`GET /runs/{run_id}/journeys` lists primary journeys a page at a time (`offset`, `limit` up to 500, and an optional `variant` id from the overview). `GET /runs/{run_id}/journeys/{trajectory_id}` returns one journey as a self-contained bundle: the trajectory, its alternatives or rollouts, and the events, objects, links, state changes, and samples they reference. Both work for small runs too. Notes on a large run are validated against its files, and a re-run resolves each note's target to its event or trajectory type instead of loading the parent.

## Targets and shares

`target_kind` sets what `target_trajectory_count` counts. `prompts`, the default, draws that many prompts. `accepted_groups` needs `group_size` above 1 and counts groups the dynamic sampler accepts, that is groups with at least one passing and one failing rollout. The job oversamples: the first batch assumes a 60% acceptance rate, each later batch is sized from the rate observed so far plus a 10% margin, and a part stops after drawing five times its target, recording `limited_by: "acceptance"`.

`domain_shares` maps some or all of the run's sub-domains to positive weights, normalized when the run is created. Each named sub-domain becomes its own part, generated with only that sub-domain in scope and given a share of the target by largest remainder; without shares, the whole run is one part and one journey can cross several sub-domains. Each part has its own seed, so resuming a crashed run reproduces it.

`generation.target` reports `kind`, `requested`, `reached`, and one entry per part with `sub_domains`, `target`, `generated`, `accepted`, and `stopped_by`. A run with a target kind of accepted groups or with shares is always generated in batches, whatever its size.

## Episodes

A bundle's `episodes` turn decisions into agent tasks. An `Episode` names its `trajectory_id` and `sample_id`, the `decision_index` (events before the decision), the `state` the machines had reached (`object.dimension` to state), the `history` of earlier events, a `task`, its `tools` (`ToolSpec`: a BIAN service domain and action, a JSON Schema for arguments, the events it records, and an optional `http` operation from the study's API definition), the `rubric` (`format`, `legality`, `grounding`, `decision`, `report`), a `skeleton` (legal events and operations, the case's objects, the step taken, and the alternatives), and `rollouts`. A `Rollout` has a `policy` (`reference`, `alternative`, `perturbed:illegal`, `perturbed:wrong_object`, or `provider:<model>` for a provider model's turn), turns (system, user, an assistant tool call, the tool result, and a final assistant message; only the assistant turns are trainable), the event it records, whether it was legal, its outcome, rubric scores, a reward (verification times the mean rubric score), and a group-relative advantage. A provider rollout also carries `checks`, what code found in its call against the skeleton: `tool_known`, `arguments_valid`, `legal`, `grounded`, `single_call`, the `event` it records, and its `branch` (`observed` when a scripted rollout took that step, `unobserved` for another legal one); a turn without a call has no tool call or result. `generation.episodes` counts episodes, rollouts by policy, accepted groups, and episodes whose tools follow the study's API, and `generation.episodes.provider` reports a run's provider calls: `limit`, `calls`, `rollouts`, `skipped_rollouts`, `stopped_by` (`budget` or `errors`), `errors`, `last_error`, and `checks` counts (`legal`, `grounded`, `observed_branch`, `no_call`).

Export writes `episodes.jsonl` and one file per harness, `openai`, `anthropic`, and `react`; the manifest's `episodes.harnesses` names the held-out one.

## Decision records

A bundle's `decisions` are its outcome decisions: in each primary journey, the first step of each outcome group where the machines left more than one outcome open. A `DecisionPoint` names its `trajectory_id`, `sample_id`, `decision_index`, and `decision_event_id`, the outcome `group` and the object kind it decides (`subject`), the `state` before it (`kind.dimension` to state), `facts` derived from the history (`events_so_far`, `days_since_start`, `days_since_previous_step`, `last_event`, `credited`, `debited`, `net`, `currency`, and `times.<event>` counts), the `history`, the case's `objects`, the `options`, a `distractor` (an outcome the state forbids, with the precondition it misses), the outcome `taken`, the journey's `outcome` (`journey_success`, `journey_type`, `final_event`, `steps_after`), `value_samples`, the pack's `goal`, and `counterfactual_basis: generator_policy`. A `DecisionOption` has its `option_id` and `event_type`, a `label` in the run's language, its `probability` (the generator policy's share among the options, after any calibration), its `value` (the share of `value_samples` simulated continuations under that policy that reach the goal), and the preconditions that make it legal (`requires`). `generation.decisions` counts points by group, records, and points with a distractor, and gives the mean value of the outcome taken.

Export writes each point as `DecisionRecord` lines (`schema_version: decision-record/1`): a `choice` whose `target_distribution` is the policy's shares (`target_basis: generator_policy_share`), a `true_false` for the outcome taken and one for the distractor (`machine_rules`, with a `rationale`), and a `score` per option whose `target_score` is its value (`simulated_goal_share`). Each carries `question`, `criteria`, `options`, an `abstain` answer that is never a target, `state`, `facts`, `history`, `outcome`, and a `prompt` rendering for language models, and appears three times: `original`, `reordered` (keys and options in another order, which the line keeps), and `paraphrase`, the variants naming the original in `variant_of` and sharing its target and split. `split` is the sample's split renamed: train, calibration (validation), or held_out (test or heldout). `decision-record.schema.json` is the JSON Schema of `DecisionRecord`, and the manifest's `decisions` section adds the schema version, the split counts and mapping, and the question types.

## Signals and evaluation

`sectors.scorers.Scorer` scores a path, given its event types and the waits between them, with each signal: `outcome`, `solution_rubric`, `behavior_rubric`, `process_conformance`, and `decision_score`. The last is computed only when the run's signal is `decision_score` or the run records decisions. Each verdict is `{"score", "passed", "items"}`, and a sequence carries all of them in `signals`. The run's `signal_mechanism` sets the sequence's `outcome`, and so its reward, advantage, and the group's acceptance. `solution_score` and `behavior_score` are the solution and behavior rubrics' scores. `generation.rewards` adds `signal` and `signals`: per signal, `mean`, `pass_rate`, `sequences`, and `pass_at_k` over each prompt's group, merged across a large run's batches. `generation.episodes.models` gives each provider model's `pass_at_k` over the episodes it attempted.

`tasks.jsonl` holds one line per prompt (`kind: journey`) and per episode (`kind: agent_episode`):
- **Journey task:** carries `prompt`, `opening`, `references` (each sequence's events, times, and verdicts), `verifiers`, the run's `primary_verifier`, and `results.generator` per verifier.
- **Agent task:** carries an `environment` (system text, task, state, tools, objects, legal events, and `responses` to each step a journey took), `verifiers` with their parameters (`legal_events`, `objects`, `passing_events`), the reference rollouts, and `results` per policy.
- **Results:** each result has `attempts`, `passes`, `mean`, and `pass_at_k`. An agent attempt passes when format, legality, grounding, and decision all hold.

`evaluation.json` gives avg@k and pass@k averaged over tasks with at least k attempts, by verifier and by policy, with the metric definitions, the verifiers, and the pack's environment. The manifest's `evaluation` section repeats the summaries.
