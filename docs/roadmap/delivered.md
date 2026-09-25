# Delivered slices

What each merged pull request established, and the decisions inside it that later work depends on. All six are merged and their branches are deleted.

`main` history: `8b00aa7`, `f4f620c`, `ba1d02c`, `95012cb`.

## 1. Scaffold the banking trajectory studio

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/1

Established the two-layer contract that everything else fills. The domain layer is object-centric: `objects`, `relationships`, `events`, `event_objects`, `state_transitions`, and `trajectories`, with `observation_status` drawn from observed, derived, imputed, or simulated. The training layer follows the MiMo hierarchy of Sample, Sequence, Context, and Segment, and only assistant segments carry `trainable=True`.

Also landed: accounts with admin, user, and demo kinds; credentials encrypted with `CREDENTIAL_MASTER_KEY` and surfaced only as provider, label, and fingerprint; platform scope restricted to admin while user and demo must bring their own key; projects, corpus upload, and runs; the judge client against `llm_inference_engine`; and the studio shell with the compose, inspect, feedback, and run-again loop. A labeled banking fixture in `packages/trajectory_contract/src/trajectory_contract/fixture.py` fills the canvas before generation exists.

## 2. Generate banking trajectories from study settings

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/2

Added `banking-semi-markov-v1`, a constrained semi-Markov generator. Domain order is fixed, dwell times vary, and hard checks enforce the invariants locally before the judge is called: card issuance precedes activation, no loan is disbursed without approval, and so on. Feedback with stance keep, revise, or drop steers the next pass, and a re-run copies the configuration, records `parent_run_id`, and inherits the selected notes. The studio materializes at most sixty-four primary trajectories per run.

## 3. Run provider web search and steer banking journeys from the report

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/3

Deep search against four providers, each with its real request shape rather than a wrapper: OpenAI and xAI post to `/v1/responses` with a `web_search` tool, Anthropic posts to `/v1/messages` with `web_search_20250305`, and Google posts to `generateContent` with `google_search` and the key in the `x-goog-api-key` header rather than the URL. The response is scrubbed of emails, secrets, and long digit runs, then stored as a corpus item of kind `deep_search` with provenance `provider:{id}`.

The important boundary: the generator does not call the provider. The scrubbed report steers the existing generator by contributing matched terms such as currency, channel, product, and named events from that sector's namespace. A drop note still removes an event the report named.

## 4. Add a retail insurance pack on the same run schema

https://github.com/caglarsubas/domain-trajectory-data-generation/pull/4

Added `insurance-semi-markov-v1` across quoting, underwriting, policy administration, billing, claims, servicing, and complaints, with its own hard checks: a policy exists before acceptance, premium and claim events follow issue, and a claim is assessed before it is settled or denied.

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
