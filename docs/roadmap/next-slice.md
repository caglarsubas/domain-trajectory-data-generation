# Next slice: trustworthy journeys

Status: approved on 25 September 2026, together with the slice order in the overview. It replaces the export, groups, and rewards slice approved on 24 September, which stays below as the queued Slice 2 with revisions. Slice 0 is a short prerequisite. The six decisions it depended on were settled the same day, all as recommended; see [Decisions](overview.md#decisions).

Branch off `main` at `3c46d4c`.

## Why the order changed

The earlier plan assumed the generated journeys were worth exporting and needed only groups and real rewards. Running the generators on `main` shows they are not ready, and the judge that would score them cannot be reached. Every item below was reproduced in memory against `3c46d4c`.

- **Eight journeys, repeated.** Variants are assigned in rotation (`packages/sectors/src/sectors/banking/generate.py:332`). A request for 1,000 journeys across all seven banking sub-domains returns 64, the studio cap, holding 8 distinct event sequences.
- **Length by repetition.** `_pad` fills a short journey by repeating events (`generate.py:999-1024`). With `min_events=20` in consumer credit, the journey ends in nine consecutive `loan.disbursed` events.
- **Contradictions pass the checks.** Warm-start text reading "the kyc failed and the application declined" forces those events into journeys that already hold the opposite outcome. The result is `kyc.passed, kyc.failed, application.approved, application.declined, account.opened, account.funded`, and `banking_hard_checks` returns no errors. Banking checks two lifecycle rules (`banking/checks.py:75-81`). Insurance has the same forced-insertion path.
- **Substring steering.** Terms are matched with `in` on lowercased text (`banking/corpus.py:63-70`), so "industry" sets the currency to TRY, "discard" names a card, and "branching" names the branch channel.
- **Reviewer text becomes training text.** Revision comments are appended to the narrative (`generate.py:750-751`), and the narrative is split into trainable assistant segments.
- **Two unlinked layers.** A `Sample` has no trajectory id (`packages/trajectory_contract/src/trajectory_contract/models.py:107-110`), and alternative trajectories get no sample. The contract promises two linked layers.
- **One language.** Only `tr` produces non-English text (`generate.py:748`). German, French, Spanish, and Japanese requests produce English.
- **Settings that change only text.** `signal_mechanism`, `consumer`, and `target_family` appear only in the system segment ("Reward X. Signal Y."). With the same seed the output is otherwise identical.
- **A judge that cannot answer.** The configured engine address ends in `/v1.`, and the client appends `/v1/evals/run` (`apps/api/app/judge.py:46, 66`). The client never sends `judge_model`, so the engine's default `llama3.2:3b` would judge. It gives up after 60 seconds, well before the engine's 240, and an engine error becomes a 500 (`apps/api/app/routes.py:474-487`). Only the first primary journey is judged (`apps/api/app/evaluation.py:67-69`), and a second cycle re-judges the same candidate.

Exporting now would ship eight sequences with repeated events and contradictions, under a placeholder reward. Group rewards need variance within a group, and the reward terms need a judge that answers. So the credential and judge wiring come first, then journeys a trainer could accept, then groups, rewards, and export.

## Slice 0: rotate credentials and fix the judge wiring

1. Rotate the engine credential. Issue a new key for `domain-trajectory-data-generation-primary` in the engine's key file, replace the public tunnel address, and update the local `.env`. This is an operator step.
2. Normalize `INFERENCE_ENGINE_BASE_URL`: strip a trailing `/`, `/v1`, or `/v1.`, and refuse to start with a clear message when it still does not parse.
3. Add `INFERENCE_ENGINE_JUDGE_MODEL`, default `qwen3.8:27b`, and send it as `judge_model` on every call.
4. Wait longer than the engine's 240-second completion timeout. Retry once on 429 and 503, honouring `Retry-After`. Map engine failures to 502, 503, or 504 with the engine's `x-request-id`, instead of letting them surface as 500.
5. Fix the Compose default for `INFERENCE_ENGINE_KEY_ID`, which is currently the literal `[REDACTED]` (`docker-compose.yml:33`), and align the variable names between `.env.example` and `apps/api/app/settings.py`.
6. Refuse to start with the default `JWT_SECRET` unless a development flag is set.
7. Add `DELETE /credentials/{id}` and key replacement that keeps the label. Today stored keys cannot be removed or rotated.
8. Tests: base-address normalization, judge error mapping, key deletion and replacement, and owner checks.

Exit: a live evaluation succeeds from the Compose stack with the new key.

Result, 25 September 2026: done. The key was rotated, and the old key now gets 401 from the engine. A live evaluation through the studio API reached the engine through the configured address, which still ends in `/v1.`, and got verdicts from `qwen3.8:27b` in about a minute. The same run exposed empty verdicts from the engine, so Slice 0 also stops treating an unreadable verdict as a score of 0. It leaves the rubric unscored, adds no revision note, and says so on the run page.

## Slice 1: trustworthy journeys

Slice 1 ships in three pull requests: the lifecycle engine and its hard checks; warm-start steering, the domain and training layers, and quality report v1; then the studio views.

Progress, 25 September 2026: the first pull request replaces both generators with the shared engine (tasks 1 to 5) and also moves reviewer notes out of trainable text (task 9). On all seven banking sub-domains, 64 journeys now hold 57 to 62 distinct event sequences across ten seeds, against 8 before (insurance: 41 to 58), and a property-based sweep of 500 random configurations across both packs finds no rule violations. Steering still matches terms by substring; that and the rest are in the second pull request.

### A shared lifecycle engine

A new `packages/sectors/src/sectors/lifecycle.py` replaces the variant templates, `_repair`, and `_pad` in both packs.

- **State machines.** Each pack declares orthogonal state machines per object type. Banking: relationship, KYC, application, account, card, credit, and complaint, following the GPT report's state model. Each transition names its event type, its guards (preconditions on other machines), and whether it is terminal.
- **Exclusive outcomes.** An application is approved or declined, never both. KYC passes or fails. A closed account emits nothing further.
- **Semi-Markov sampling.** At each step the sampler lists the legal next events, weights them with transition priors per sub-domain, and samples a dwell time from a per-transition distribution, log-normal by default. Priors are set by hand in this slice and calibrated from data sources in Slice 5.
- **Length is a constraint on sampling.** A journey that ends too early is resampled or extended with legal events. Nothing repeats unless the domain repeats it, such as card purchases, payments, or document resubmissions within a cap.
- **Branch points.** A branch point is any step where the machines offer more than one legal outcome. Alternatives keep the most likely continuation, stochastic samples, and a rare but valid path when requested. The branch probability comes from the sampler instead of a uniform draw between 0.18 and 0.42, and each alternative records `causal_claim=false`.
- **Journey starts.** Start times follow a weekday and hour profile instead of a grid of one journey every three days.

### Hard checks from the same machines

The checks are generated from the pack's state machines and run independently on the output, so every rule the sampler obeys is verified twice. Banking gains, among others: an application precedes account opening, KYC passes before an account opens or a card activates, no event follows closure, the loan itself is approved before disbursement, and a payment needs an active account. Insurance moves its six rules onto the same machines. A property-based sweep over thousands of random configurations asserts zero violations for both packs.

### Warm-start steering that cannot contradict

- Terms are matched on word boundaries and phrases, not substrings.
- Event mentions are negation-aware, so "no KYC failure" does not name `kyc.failed`.
- Named events weight the sampler's choices at branch points instead of being forced into a sequence. A corpus naming both outcomes of a decision produces journeys of both kinds, and no journey holds both.
- Each run stores a steering report: which terms matched, in which document, and what they changed.
- The composer warns when warm documents yield no readable text, which is true of every PDF until Slice 5.

### Domain-layer fidelity

- Money carries amount, currency, direction, and role. Today every amount is positive and there is no direction field.
- Event-object links carry qualifiers, such as the source and destination account of a transfer.
- `effective_time` differs from `event_time` where the domain says so, such as payment posting, and the `recorded_at` lag is sampled rather than fixed at two seconds.
- Every run records the generator and pack versions.

### Training layer

- `Sample.trajectory_id` links each sample to its journey, and alternative trajectories get samples too.
- Reviewer notes are kept in generation metadata and never enter trainable text.
- Segments split at sentence boundaries, and user turns interleave with assistant turns.
- Prompts and user lines come from a larger template bank per sub-domain and language, so a run no longer has a single prompt.
- Both packs declare English and Turkish. Any other language is refused with a message instead of producing English.
- Text stays template-based in this slice, and no provider is called. Provider-written turns arrive with the episode builder in Slice 6.

### Quality report v1

Computed on every run and stored with it:

- **Complete:** hard-check violations, which must be zero; referential integrity; the share of journeys reaching a terminal state; and runs of identical events outside allowed repeats.
- **Comprehensive:** distinct sequences per hundred journeys, event-type coverage per selected sub-domain, the rare-path share, and transition bigram coverage.
- **Representative** and **qualitative** are shown as "not measured yet" until Slices 4 and 5.

### Studio

- The run page gains time-axis swimlanes per object (customer, application, account, card, loan), a process map of the run with transition counts, a variant explorer showing the top sequences and their frequency, and the quality scorecard.
- The composer shows the cap of sixty-four before generating, gates Generate on the acknowledgment and the document count, and shows how many warm documents were readable.
- A re-run keeps the files and links added during it. Today they are dropped (`apps/web/app/studio/compose/page.js:150-177`).
- A study can be reused across runs, instead of the composer creating a new study each session.
- Trajectory-level feedback, which the API already accepts, gets a place in the UI.

### Task list

1. Write `lifecycle.py`: machines, guards, exclusivity, terminal states, and the semi-Markov sampler with dwell-time distributions.
2. Express the banking pack as machines and priors, and delete the variant templates, `_repair`, and `_pad`.
3. Generate banking hard checks from the machines, and add the property-based sweep.
4. Port insurance to the same engine and checks.
5. Rebuild branch generation on branch points, with sampler probabilities and `causal_claim=false`.
6. Rewrite corpus steering: word-boundary matching, negation, weights instead of forced events, and the steering report.
7. Add direction and role to money, qualifiers to event-object links, realistic `effective_time` and `recorded_at`, and start-time profiles.
8. Add `Sample.trajectory_id`, samples for alternatives, sentence-boundary segments, and interleaved user turns.
9. Move reviewer notes out of trainable text.
10. Expand the prompt and user-line template banks in English and Turkish, and refuse other languages at the API and in the composer.
11. Compute quality report v1, store it on the run, and return it from `GET /runs/{id}`.
12. Build the time axis, process map, variant explorer, and scorecard on the run page.
13. Fix the composer: show the cap, gate Generate, show readable documents, keep re-run uploads, reuse studies, and add trajectory feedback.
14. Update `docs/trajectory-contract.md` and `docs/evaluation.md` to match.

### Exit criteria

- Zero hard-check violations across the property-based sweep, for both packs.
- 64 journeys over all seven banking sub-domains hold at least 32 distinct event sequences. Today they hold 8.
- No journey contains a run of identical events outside the allowed repeats.
- A corpus that names both outcomes of a decision yields journeys of both kinds, and none holding both.
- No reviewer text appears in a trainable segment, and every sample links to a trajectory.
- The quality report and the new run-page views are visible in the studio, and the composer shows the cap before generating.

## Queued: Slice 2, groups, shared rewards, and export

The design approved on 24 September stands, with the revisions after it.

### Retained design

- **Groups.** `RunBody` and `RerunBody` gain `group_size`, default 1 and capped at 16, the group size MiMo trains with. Each generator draws G variants of one prompt from its seeded sampler, varying branch choices and dwell times, so a group holds different outcomes for the same request and stays reproducible.
- **Contract fields.** Nullable only, so stored bundles keep validating: `group_accepted` and `group_pass_rate` on Sample; `outcome`, `solution_score`, `behavior_score`, `quality_factor`, `token_estimate`, and `dropped` on Sequence; `dropped` on Context; `flagged_reason` on Segment.
- **Rewards.** One `packages/sectors/src/sectors/rewards.py`, shared by every pack, replaces both copies of `_apply_rewards`:
  - the multiplicative reward R = R_test × S_sol × S_beh, where a failed verification zeroes the reward;
  - group-relative advantage, each reward minus the group mean;
  - groupwise advantage redistribution over the passing set, which rescales quality-weighted positive advantages by λ = ΣA / ΣfA so the positive mass is conserved, with λ capped and failing sequences untouched;
  - the group-relative length penalty, gated on the minimum pass rate and measured against a percentile of successful lengths, so hard prompts keep room to explore;
  - segment penalties that mask flagged segments in positive sequences and weight them by κ in negative ones, with α and β bounded;
  - the cascade: a context with no surviving trainable segment is dropped, a sequence with no surviving context gets zero advantage, a sample with no surviving sequence is rejected, and all-pass and all-fail groups are marked not accepted.
- **Export.** `GET /runs/{run_id}/export/{part}`, owner-checked like `require_run` in `apps/api/app/service.py`, returning 409 when the run has no candidate.
  - `samples.jsonl`: one line per Sample, with the hierarchy, `trainable`, and the group fields.
  - `domain.jsonl`: one line per domain record.
  - `manifest.json`: run id, sector, generator and pack versions, the configuration without `credential_id`, group size, counts, judge cycles, split ratios, and a statement that every record is synthetic. No secret, ciphertext, or fingerprint reaches it.
  - The train, validation, and test split is assigned from a hash of `sample_id`, so a re-export reproduces it. One sub-domain can be held out to measure generalization.
- **Tests.** Each formula against hand-computed values, advantage mass conservation, the pass-rate gate exempting hard groups, and the cascade, in the style of `tests/test_generator.py`.

### Revisions from this review

- **Define the reward terms.** R_test is the code verification of the sample's goal: a legal journey that reaches the requested outcome. S_sol and S_beh start as deterministic measures, goal attainment and conformance to the pack's machines, and switch to judge rubric scores when Slice 4 makes the judge trustworthy.
- **Add `ocel.json`.** The domain layer in OCEL 2.0 JSON: event types, object types, events, objects, and qualified event-object and object-object relationships. Both research reports recommend it, and process-mining tools read it directly.
- **Add a data card to the manifest.** Scope, jurisdiction profile, intended use, known limitations, generator and pack versions, and the quality report.
- **Detect before penalizing.** MiMo's penalty module separates rules, which detect, from strategies, which mask, shape advantages, or only record. Every penalty starts in record-only mode and is shown in the group viewer, so a user sees what would change before it changes the training data.
- **UI.** A group viewer with the G sequences side by side, reward and advantage per sequence, and a download panel with the data card.

## Out of scope for Slice 1

Groups, rewards, and export (Slice 2). Background jobs and lifting the cap (Slice 3). Judge reliability (Slice 4). Document parsing and calibration (Slice 5). Episodes and decision records (Slice 6). New sectors (Slice 7).

## What the attached material contributed

- **The MiMo PDF** was checked again. It holds 320 link annotations from its own cross-references and no highlight, note, or ink annotations, and its pages contain no flattened highlight marks. The copy in Downloads is byte-identical to the one in `docs/`. If you annotated it in another app, exporting the annotations from that app would bring them in.
- **From MiMo:** the trajectory hierarchy and penalty module (§6.1), together with groupwise reward synthesis, groupwise advantage redistribution, and the behavioral penalties (§4.3), shape Slice 2. Rollout auditing and repeated or cross-model judging (§4.2.1 and §4.2.2) shape Slice 4. Environment synthesis with atomic rubric items (§4.2.2), multi-harness training (§4.2.5), and prefix-conditioned distillation (§5.6) shape Slice 6. The Sample Mixer (§6.3) shapes the oversampling in Slice 3.
- **From the GPT banking report:** orthogonal state machines, the time model, the rules for money, the validation order, and the rules for alternative paths shape Slice 1. The documentation-to-knowledge pipeline, with explicit and strongly implied facts, and the calibration sources shape Slice 5.
- **From the Gemini banking report:** OCEL 2.0 JSON export shapes Slice 2. The BPI Challenge 2017 baseline and the conformance measures shape Slices 4 and 5; the scorecard computes fitness and precision in-house. The MDP framing, with BIAN operations as actions, shapes the decision records in Slice 6.
