# Delivered slices

What each merged pull request established, and the decisions inside it that later work depends on. All sixteen are merged and their branches are deleted.

`main` history: `c0cb37a`, `8e8f664`, `f73b75d`, `f9edc37`, `4002c1f`, `eb16284`, `6ced1a4`, `1a6fe6f`, `2410de8`, `3c46d4c`, `8b00aa7`, `f4f620c`, `ba1d02c`, `95012cb`.

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
