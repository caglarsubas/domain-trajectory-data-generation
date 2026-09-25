# Evaluation feedback cycle

The platform judges candidate trajectories. The judge is the platform tenant on `llm_inference_engine`, not a user-uploaded key.

Identity:

- Tenant: `domain-trajectory-data-generation`
- Organization: `org-trajdata`
- Key ID: `domain-trajectory-data-generation-primary`

Environment variables, set in `.env` and never committed:

- `INFERENCE_ENGINE_BASE_URL`
- `INFERENCE_ENGINE_API_KEY`
- `INFERENCE_ENGINE_TENANT`
- `INFERENCE_ENGINE_ORG_ID`
- `INFERENCE_ENGINE_KEY_ID`

Each cycle runs that sector's local hard checks first. A failed check does not call the model. When checks pass, the client posts to `/v1/evals/run` with a bearer token and one of:

- `helpfulness` — representativeness for the chosen sub-domain and language
- `correctness` — agreement with transitions supported by the warm-start corpus; a cold start sends a shorter expected brief and is marked `reference_quality=weak`
- `safety` — synthetic data only, no personal or real account identifiers
- `pairwise_quality` — only when the candidate has a parent trajectory and an alternative branch

Each call names its judge with `judge_model`, taken from `INFERENCE_ENGINE_JUDGE_MODEL` (default `qwen3.8:27b`). `INFERENCE_ENGINE_BASE_URL` is the engine origin; a trailing `/`, `/v1`, or `/v1.` is removed, and the API refuses to start when the value does not parse. The client waits up to 300 seconds, longer than the engine's own completion timeout, and retries once when the engine answers 429 or 503 with a `Retry-After` of 30 seconds or less. When the engine fails, the studio answers 502, 503, or 504 with the engine's request id, and no cycle is stored.

A verdict the engine could not parse is not a score. The rubric is left unscored, the cycle is not accepted, and no revision note is added for it.

Verdicts are stored on the run. Scores under the run thresholds become revision notes. Another cycle is allowed until `max_cycles`.

Human notes are separate. A note targets the run, one trajectory, or one event, with stance `keep`, `revise`, or `drop`. Re-run copies the configuration, `parent_run_id`, and the selected note ids, then regenerates events from those notes and from any revision notes on the parent. A dropped event type is left out of the next bundle. A revised event type is delayed. A kept event type is retained when it still fits the length limit.

Provider deep search uses the selected account key to call that provider's web search, then stores a scrubbed report on the study. Named events in the report are kept on the next generation when they belong to the selected sub-domains of that sector. A drop note still removes an event the report named. The generator does not call the provider.
