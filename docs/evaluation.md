# Evaluation feedback cycle

The platform judges candidate trajectories. Generation does not run in this slice. The judge is the platform tenant on `llm_inference_engine`, not a user-uploaded key.

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

Each cycle runs local banking hard checks first. A failed check does not call the model. When checks pass, the client posts to `/v1/evals/run` with a bearer token and one of:

- `helpfulness` — representativeness for the chosen sub-domain and language
- `correctness` — agreement with transitions supported by the warm-start corpus; a cold start sends a shorter expected brief and is marked `reference_quality=weak`
- `safety` — synthetic data only, no personal or real account identifiers
- `pairwise_quality` — only when the candidate has a parent trajectory and an alternative branch

Verdicts are stored on the run. Scores under the run thresholds become revision notes. Another cycle is allowed until `max_cycles`.

Human notes are separate. A note targets the run, one trajectory, or one event, with stance `keep`, `revise`, or `drop`. Re-run copies the configuration, `parent_run_id`, and the selected note ids, then regenerates events from those notes and from any revision notes on the parent. A dropped event type is left out of the next bundle. A revised event type is delayed. A kept event type is retained when it still fits the length limit. Provider deep search stays unimplemented.
