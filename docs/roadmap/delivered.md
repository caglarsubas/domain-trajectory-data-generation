# Delivered slices

What each merged pull request established, and the decisions inside it that later work depends on. All twenty-seven are merged and their branches are deleted.

`main` history: `a23cdbc`, `31846b5`, `0920121`, `32b564b`, `a578aa4`, `911840f`, `04cf306`, `f8a8199`, `d580d52`, `4bddbcc`, `d0fb3ea`, `8391bad`, `c0cb37a`, `8e8f664`, `f73b75d`, `f9edc37`, `4002c1f`, `eb16284`, `6ced1a4`, `1a6fe6f`, `2410de8`, `3c46d4c`, `8b00aa7`, `f4f620c`, `ba1d02c`, `95012cb`.

Corrected on 25 September 2026 against the code: slice 2 checks two lifecycle rules, not a general set, and slice 4's first insurance rule was stated backwards.

## 1. Scaffold the banking trajectory studio

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/1

Established the two-layer contract that everything else fills. The domain layer is object-centric: `objects`, `relationships`, `events`, `event_objects`, `state_transitions`, and `trajectories`, with `observation_status` drawn from observed, derived, imputed, or simulated. The training layer follows the MiMo hierarchy of Sample, Sequence, Context, and Segment, and only assistant segments carry `trainable=True`.

Also landed: accounts with admin, user, and demo kinds; credentials encrypted with `CREDENTIAL_MASTER_KEY` and surfaced only as provider, label, and fingerprint; platform scope restricted to admin while user and demo must bring their own key; projects, corpus upload, and runs; the judge client against `llm_inference_engine`; and the studio shell with the compose, inspect, feedback, and run-again loop. A labeled banking fixture in `packages/trajectory_contract/src/trajectory_contract/fixture.py` fills the canvas before generation exists.

## 2. Generate banking trajectories from study settings

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/2

Added `banking-semi-markov-v1`, a constrained semi-Markov generator. Domain order is fixed, dwell times vary, and hard checks run locally before the judge is called. Besides structural integrity, they enforce two lifecycle rules: card issuance precedes activation, and no loan is disbursed without approval. Journeys come from eight variants assigned in rotation. Feedback with stance keep, revise, or drop steers the next pass, and a re-run copies the configuration, records `parent_run_id`, and inherits the selected notes. The studio materializes at most sixty-four primary trajectories per run.

## 3. Run provider web search and steer banking journeys from the report

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/3

Deep search against four providers, each with its real request shape rather than a wrapper: OpenAI and xAI post to `/v1/responses` with a `web_search` tool, Anthropic posts to `/v1/messages` with `web_search_20250305`, and Google posts to `generateContent` with `google_search` and the key in the `x-goog-api-key` header rather than the URL. The response is scrubbed of emails, secrets, and long digit runs, then stored as a corpus item of kind `deep_search` with provenance `provider:{id}`.

The important boundary: the generator does not call the provider. The scrubbed report steers the existing generator by contributing matched terms such as currency, channel, product, and named events from that sector's namespace. A drop note still removes an event the report named.

## 4. Add a retail insurance pack on the same run schema

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/4

Added `insurance-semi-markov-v1` across quoting, underwriting, policy administration, billing, claims, servicing, and complaints, with its own hard checks: underwriting acceptance precedes policy issue, premium and claim events follow issue, and a claim is assessed before it is settled or denied.

This is the slice that proved the sector boundary works. The run schema did not change. `SectorPack` gained a `generate` method, `candidate_for_run` dispatches through `get_sector(config["sector"]).generate`, and the `sector` literal widened. Every future pack follows this shape.

The merge commit `8b00aa7` also carried the Docker Compose work from pull requests 5 and 6, because both were stacked on this branch.

## 5. Build the studio with Docker Compose

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/5

Postgres 16, the API on `python:3.12-slim` under uvicorn, and the studio on `node:22-alpine`. The API waits for a healthy database and creates tables on startup. Compose fills local defaults for `CREDENTIAL_MASTER_KEY`, `JWT_SECRET`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` when `.env` leaves them empty, while the inference-engine key still comes from `.env`.

## 6. Keep studio sign-in working when host port 8000 is taken

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/6

Two defects found by running the stack on a laptop rather than only in the cloud workspace.

The browser was calling `http://localhost:8000` directly, so opening the studio on any host other than localhost failed with a fetch error before the password was ever checked. The studio now proxies API calls through its own server at `/backend`, which forwards to `http://api:8000` inside the Compose network.

Host port 8000 was already bound on the laptop, so the API container never joined the network at all. Compose now publishes the API on host port 18000, overridable with `API_HOST_PORT`.

Verified end to end in a browser against a non-localhost host: `admin@example.com` signs in and reaches the admin-only Platform page.

## 7. Write the roadmap into docs

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/7

Moved the roadmap out of a chat session and into the repository as three files: this record, the overview with the purpose and standing constraints, and the next slice. The README links `docs/roadmap`.

## 8. Re-plan the roadmap around trustworthy journeys

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/8

Re-planned after running the generators on `main`: 64 journeys held 8 distinct sequences, short journeys were padded by repetition, and contradictory journeys passed the hard checks. The overview maps the purpose to capabilities and slices, defines the four quality words as measurements, and records the approved slice order and six decisions. jev-xai was left out at your request; decision records will use the platform's own schema.

## 9. Reach the judge reliably and let owners replace or remove keys

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/9

Slice 0. The engine address is reduced to its origin, each judge call names its model (`qwen3.8:27b` by default), waits longer than the engine's own timeout, retries once on a short `Retry-After`, and reports engine failures as 502, 503, or 504 with the engine's request id. An unreadable verdict is left unscored instead of counting as 0. Key owners can replace a secret or delete a key. The development JWT secret is refused outside `TRAJ_DEV_MODE`.

Verified live against the engine: evaluations returned verdicts through an address still ending in `/v1.`. The same runs showed the engine returning empty verdicts for longer prompts, recorded as an engine dependency in the overview.

## 10. Generate journeys from state machines and check them against the same rules

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/10

Slice 1, first part. Both packs declare their events as transitions on orthogonal state machines, and one shared engine walks only legal transitions with log-normal dwell times. The hard checks replay every trajectory through the same machines. On all seven banking sub-domains, 64 journeys went from 8 distinct event sequences to 57 to 62. Alternatives branch at a real decision point and record `causal_claim: false`; warm-start events weight the sampler instead of being forced in.

## 11. Steer from whole words, give money a direction, and report quality on every run

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/11

Slice 1, second part. Whole-word, negation-aware steering with a report per document; money direction and role, event-object qualifiers, realistic effective and recorded times, and weekday and hour start profiles; samples linked to their trajectories with sentence-aligned turns; languages refused outside English and Turkish; and quality report v1 on every run.

## 12. Show quality, variants, a process map, and a time axis for every run

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/12

Slice 1, third part, completing the slice. The run page gains the quality scorecard, a process map, a variant explorer, and a time axis with the alternative drawn dashed; journeys take their own notes. The composer reuses studies, shows readability per document, offers the pack's languages, shows the cap, lists blockers, and keeps re-run uploads. `/sectors` carries languages, the cap, lanes, and event kinds.

## 13. Draw groups of sequences per prompt and score them with shared MiMo rewards

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/13

Slice 2, first part. `group_size` up to 16, rollouts sharing the prefix to the first decision and one intent, and `rewards.py` with the multiplicative reward, group-relative advantage, advantage redistribution, the gated length penalty, segment penalties, and the cascade, each checked against hand-computed values. Penalty rules run in record-only mode.

## 14. Export runs in four parts and show each group's rollouts in the studio

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/14

Slice 2, second part, completing the slice. `GET /runs/{id}/export/{part}` serves `samples.jsonl`, `domain.jsonl`, OCEL 2.0 `ocel.json`, and a `manifest.json` with a data card and SHA-256 checksums. The split comes from `sha256(run_id|sample_id)`, so a re-export reproduces it, and a held-out sub-domain is chosen by its milestone events. The studio gains a group viewer and a download panel, and the composer sets the group size.

## 15. Generate runs as jobs with progress and cancellation

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/15

Slice 3, first part. A `jobs` table on the application database, run inline on SQLite, by a thread in the API on Postgres, or by a `worker` Compose service; progress, cancellation before and during a run, requeueing of jobs whose worker stopped, and a run list that no longer loads bundles. Four concurrent workers on Postgres never claimed a job twice.

## 16. Generate large runs in resumable batches and read them a journey at a time

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/16

Slice 3, second part. Runs up to 100,000 sequences are generated in batches of 256 with their own seeds and batch-prefixed ids, written as gzipped files under `DATA_DIR/runs/<id>/`, and checkpointed so a restarted worker resumes and matches an uninterrupted run. Journeys are served a page at a time, and quality and the overview accumulate batch by batch. A 10,000-journey run completes in about 22 seconds with no rule violations.

## 17. Export large runs as a job and size runs by accepted groups

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/17

Slice 3, third part. Large runs export through a job that streams batch by batch into gzipped parts, staged and renamed when complete, with manifest checksums of the uncompressed content. `target_kind: "accepted_groups"` oversamples from the observed acceptance rate until the target survives the dynamic sampler, stopping at five times the target; `domain_shares` split a run into parts with their own targets and seeds. The work showed that five of the seven banking sub-domains had no failing rollouts, so their groups carried no signal.

## 18. Give every banking sub-domain failing rollouts so its groups can be accepted

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/18

Follow-up to #17. Rollouts in deposits, cards and payments, servicing, and complaints could not fail, so the dynamic sampler rejected all their groups. Abandoned and declined applications, failed KYC, complaints not upheld, declined limit changes, declined card purchases, and delinquent loans are now reachable where the domain allows them, and every banking sub-domain yields accepted groups.

## 19. Judge and deep search as jobs, check keys with the provider, and add demo quotas

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/19

Slice 3, fourth part, completing the slice. Judging and deep search run as jobs with progress and cancellation, and inline they answer with their old status codes. A saved key is checked with a free authenticated call to its provider: a rejected key is refused and an unreachable provider leaves it saved but not ready. Demo accounts get daily limits on runs, judge cycles, and deep searches and a run size limit. Oversampling is capped at the run limit.

## 20. Judge a stratified sample with two models and report agreement

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/20

Slice 4, first part. A cycle judges six journeys drawn from each kind and outcome in turn, rendered with objects, amounts, state changes, and sample text inside a prompt budget. `qwen3.8:27b` and `gemma4:26b` answer every rubric, pairwise runs in both orders, and a control journey with its events out of order tests whether a judge can see a broken journey. Cycles record per-model scores, agreement, order consistency, and audit flags for likely false positives and negatives; acceptance follows the primary judge.

## 21. Regenerate from the judge's notes, show what changed, and gate export on acceptance

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/21

Slice 4, second part; the study-specific rubrics planned before it wait on the engine's rubric registry. A run the judge has read is not judged again unless its primary verdict was unreadable. `POST /runs/{id}/regenerate` makes a child run from the latest cycle's revision notes and the parent's notes, within `max_cycles` rounds, and judges it once it is generated. `generation.notes` records what every note did, `GET /runs/{id}/diff` compares a run with its parent, and the run page shows it as What changed. Export needs an accepted cycle unless `allow_unaccepted=true` is passed, which the manifest's `review` records. The diff showed that a helpfulness note removed every declined application and failed KYC from a regenerated run.

## 22. Merge the process map and the journey views into one interactive map

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/22

Follow-up to #12. The run page's process map and its time axis and sequence views of one journey become one map: event types sit on a shared axis at their typical time or step, and the chosen journey, its rollouts, and its simulated alternative are traced over it. Clicking a step or node opens the event, its objects, and the transitions around it, and a node takes keep, revise, or drop notes for every event of its type. The overview gains each type's typical hours and step and each transition's typical dwell, accumulated batch by batch; a large run stored without them keeps the sequence layout. Event feedback accepts an event type the sector knows.

## 23. Keep journeys the domain ends early when a helpfulness note asks for longer ones

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/23

Follow-up to #21. A helpfulness note raised the minimum length for every primary journey, so a banking run over onboarding, deposits, and consumer credit went from 16.7 declined applications and failed KYC checks per 100 journeys to none. The raised minimum now applies only to journeys that can go on, and one the domain ends is held to the requested minimum. Runs without the note are unchanged, and a test checks that the note keeps at least half the failure share it had without it.

## 25. Let runs start without a provider key

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/25

Generation calls no provider, so `credential_id` is optional on a run and a key is checked only when one is given; reruns and regenerations of a keyless run keep none. The composer marks the key optional. Deep search still needs one.

## 26. Read warm-start documents and links, and brief the judge with the passages that matter

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/26

Slice 5, first part. PDF, Word, HTML, Markdown, and OpenAPI and AsyncAPI definitions are parsed once and cached; links are fetched by a job behind a guard that refuses private addresses before the request, on every redirect, and at the connected address; GitHub repositories are read through their README, docs, and API definitions; and the judge's brief carries BM25-ranked passages instead of the first 2,000 characters. An uploaded file's name no longer chooses where it is stored.

## 27. Read facts from a study's documents for review, and add jurisdiction profiles

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/27

Slice 5, second part. A study's documents are read into currency, channel, product, event, and negated-event facts with evidence and a confidence: explicit ones steer, strongly implied ones steer once accepted in the composer's review queue, and what no fact covers is listed as taken from defaults. Runs choose a neutral, Turkey, or United Kingdom profile, which sets currency, local product names, KYC rules for the judge and the samples, and the starting language.

## 28. Calibrate the generator from event logs and measure how representative a run is

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/28

Slice 5, third part, completing the slice. CSV, Parquet, XES, and OCEL 2.0 logs are read, their activities mapped to the pack's events and correctable, and turned into next-step counts and duration quantiles; runs blend them with the priors by how much data backs them and follow steps through events a run leaves out. Representativeness is measured as fitness, precision, and next-step divergence. The catalogue downloads BPI Challenge 2017 and UCI Bank Marketing on demand with licence and snapshot date and recognizes a CFPB complaint export.

## 29. Open accounts only after approval, and accept a run a single unreadable pairwise order leaves scored

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/29

Two fixes found while judging a warm banking run with the local engine. `account.opened` now needs an approved application, not only a submitted one (`banking-pack-4`), and a test walks all seven banking sub-domains to check it. An unreadable verdict from the primary judge now blocks acceptance only when it leaves a sampled journey without a score for its rubric, so one readable pairwise order still scores the journey; it is still flagged.

## 30. Build agent episodes at each decision point and export them in three harness formats

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/30

Slice 6, first part. Where a group's rollouts part, an episode holds the machines' state, operations named after BIAN service domains and shaped by the study's OpenAPI definitions, a task in the run's language, five code-checked rubric items, and scripted rollouts (the step taken, legal alternatives, a forbidden operation, another case's object) rewarded as verification times rubric score, with group-relative advantages. Export writes every rollout in chat-tool, tool-block, and ReAct formats, with ReAct held out, and the run page shows the focused journey's episode.

## 31. Let provider models take episode turns on the account's own key, checked against the skeleton

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/31

Slice 6, second part of episodes. A model at OpenAI, Anthropic, Google, or xAI takes each episode's turn on the run owner's key: it calls an operation, the mock bank answers, and it reports. Code checks the call against the skeleton (a known operation, valid arguments, a legal step, the case's own objects, one call) and scores it on the same rubric, so it joins its group's advantages. The composer estimates the calls and takes a cap, failed calls count against it, three failures stop the rollouts, a large run spends one budget across its batches, demo runs make at most 100 calls, and the key never reaches an error or the run.

## 32. Record outcome decisions for decision scoring and Jev-type models, and export them as typed questions

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/32

Slice 6, decision records. Decision-scoring and Jev-type runs record the first decision of each outcome group in a journey. Each record holds the machines' state, facts precomputed from the history, each outcome's policy share and its value from 64 simulated continuations under that policy, and what the journey did. Export writes each decision as choice, true-or-false, and score questions with criteria, an abstain answer, reordered and paraphrased variants, and train, calibration, and held-out splits, validated against a versioned schema that ships with the export. History-prefix records, consumer export parts, and a decision viewer come with it.

## 33. Give each signal mechanism a scorer, and export evaluation tasks with verifiers and pass@k

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/33

Slice 6, evaluation, completing the slice. Five scorers read only a journey's events and waits: outcome, solution rubric, behavior rubric, process conformance, and decision score. The run's signal decides pass or fail and so rewards and group acceptance, while the solution and behavior rubrics become the reward's solution and behavior terms. Evaluation runs export every prompt and episode as a task with its environment, verifiers, and reference trajectories, and report avg@k and pass@k by verifier and by policy; a provider model's attempts on the owner's key become its pass@k.

## 34. Draw a group's first sequence like its rollouts, not for success

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/34

Follow-up to #23, found with the #33 scorers. A journey the domain ends, such as a declined application, was redrawn until it reached the minimum length unless it was a rollout, so on a banking run over onboarding and deposits every group's first sequence passed, at typicality 0.98, while its rollouts passed 70% at 0.83. Such journeys now count at their natural length for the first sequence too. That exposed a second coupling: banking reads intent from the product a journey reached, so a declined first sequence held every rollout to reaching none, and they failed with it. The group's intent is now the first sequence's, or else the first rollout's that reached a product, and the opening asks for it. Over five seeds the first sequence now passes as often as unselected walks, and rollouts are within four points.

## 35. Add the sector gates as code and a telecommunications pack that passes them

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/35

Slice 7, first part. `sectors.gates` checks any pack against the gates banking passes, and the suite runs them for every registered pack:
- a complete spec
- a random sweep with no broken rule
- every sub-domain reaching its milestones
- 32 distinct sequences in 64 journeys
- every event reachable
- stable seeds
- episodes, decisions, and signals

The telecommunications pack covers ordering with a credit check, activation with number porting, billing with suspension and restoration, plan changes, fault repair, retention, and complaints, with operations named after TM Forum Open API domains. Packs gained their own operation maps and agent wording, jurisdictions gained rules per sector, the API accepts any registered sector, and the composer takes each pack's default sub-domains.
