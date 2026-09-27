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
- `pairwise_quality` — only when the candidate has a parent trajectory and an alternative branch. Both journeys are shown blind (no kind, outcome, or alternative label), and the score is reported without deciding acceptance or writing revision notes.

Each call names its judge with `judge_model`, taken from `INFERENCE_ENGINE_JUDGE_MODEL` (default `qwen3.8:27b`). `INFERENCE_ENGINE_BASE_URL` is the engine origin; a trailing `/`, `/v1`, or `/v1.` is removed, and the API refuses to start when the value does not parse. The client waits up to 300 seconds, longer than the engine's own completion timeout, and retries once when the engine answers 429 or 503 with a `Retry-After` of 30 seconds or less. When the engine fails, the studio answers 502, 503, or 504 with the engine's request id, and no cycle is stored.

A verdict the engine could not parse is not a score. The rubric is left unscored, the cycle is not accepted, and no revision note is added for it.

Verdicts are stored on the run. Helpfulness, correctness, and safety scores under the run thresholds become revision notes and block acceptance; pairwise quality does neither. Another cycle is allowed until `max_cycles`.

## Repeated judgments

Every call asks the engine for `JUDGE_REPEATS` verdicts (default 3, at most 5) at `JUDGE_TEMPERATURE` (default 0.7), with seeds 0, 1, 2, and so on; one repeat is asked at temperature 0. A journey's score for a rubric is the mean of its readable repeats, and it is unscored only when none is readable. Each repeat is stored as its own verdict with its `repeat` index.

The cycle reports agreement across repeats next to agreement across models, per rubric and model:

- **calls**: calls with at least two readable repeats. Pairwise counts each order as its own call.
- **stable**: calls whose repeats all fall on the same side of the rubric's threshold, the rule agreement across models uses.
- **mean_spread**: the mean gap between a call's highest and lowest repeat, on the 0 to 1 scale.

A call whose repeats straddle the threshold is flagged `repeats_disagree`. An engine without repeats answers with one verdict, and the cycle then reports none.

## The judge against the code scorers

`process_conformance` and `decision_score` are code signals on every sequence (the decision score only when the run records decisions or uses it as its signal). The studio registers both as rubrics of the platform tenant with `POST /v1/evals/rubrics` at the start of a cycle, and asks the judge each for the journeys the code scored. The definitions are in `apps/api/app/judge_rubrics.py`: the brief and a question go in as the prompt and the rendered journey as the response, and the judge answers a 1 to 5 score that the engine normalises to 0 to 1.

The cycle's `agreement.code` gives, per rubric, the code's pass count and mean, and per model the judge's mean, how many journeys it agrees on, and the mean gap to the code's score. The judge's verdict passes at 0.5 for conformance, the code's own bar for typicality, and at 0.75 for the decision score, whose code bar is every decision within 0.9 of the best. A journey where they differ is flagged `code_disagrees`. The code's verdict stays the signal: these two rubrics measure the judge, never decide acceptance, and an unreadable verdict on them does not reopen the run.

The cycle's `judging` records the repeats, their temperature, and each registered rubric's engine digest; each verdict of a registered rubric carries that `rubric_digest`. An engine without tenant rubrics still judges the other rubrics, and `judging.notes` says what was skipped.

The engine's typed errors reach the studio as explanations: a journey too long for the judge's context window names the tokens it needed and suggests lowering `JUDGE_PROMPT_TOKENS`, and a timeout gives the engine's limit.

Human notes are separate. A note targets the run, one trajectory, or one event, with stance `keep`, `revise`, or `drop`. Re-run copies the configuration, `parent_run_id`, and the selected note ids, then regenerates events from those notes and from any revision notes on the parent. A dropped event type is left out of the next bundle. A revised event type is delayed. A kept event type is retained when it still fits the length limit.

Provider deep search uses the selected account key to call that provider's web search, then stores a scrubbed report on the study. Named events in the report are kept on the next generation when they belong to the selected sub-domains of that sector. A drop note still removes an event the report named. The generator does not call the provider.
