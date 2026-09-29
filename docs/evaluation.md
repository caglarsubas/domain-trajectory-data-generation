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

Each call names its judge with `judge_model`, taken from `INFERENCE_ENGINE_JUDGE_MODEL` (default `qwen3.6:27b`, decision 20; `qwen3.8:27b` shares an engine substitution group with the second judge, `gemma4:26b`, so it could be that model judging twice). `INFERENCE_ENGINE_BASE_URL` is the engine origin; a trailing `/`, `/v1`, or `/v1.` is removed, and the API refuses to start when the value does not parse. The client waits up to 300 seconds, longer than the engine's own completion timeout, and retries once when the engine answers 429 or 503 with a `Retry-After` of 30 seconds or less. When the engine fails, the studio answers 502, 503, or 504 with the engine's request id, and no cycle is stored.

A verdict the engine could not parse is not a score. The rubric is left unscored, the cycle is not accepted, and no revision note is added for it.

Verdicts are stored on the run. Helpfulness, correctness, and safety scores under the run thresholds become revision notes; safety always blocks acceptance, and helpfulness and correctness block it only where the primary judge saw their controls that cycle (see below). Pairwise quality does neither. Another cycle is allowed until `max_cycles`.

## Repeated judgments

Every call asks the engine for `JUDGE_REPEATS` verdicts (default 3, at most 5) at `JUDGE_TEMPERATURE` (default 0.7), with seeds 0, 1, 2, and so on; one repeat is asked at temperature 0. A repeat that comes back as an empty JSON object, as qwen3.8:27b did on one seed for pairwise, is asked once more on a seed no repeat used; if that answer is not readable either, the repeat stays unreadable. `decision_score` and the pairwise question ask for a reason under 40 words, which keeps a long answer inside the engine's budget. A journey's score for a rubric is the mean of its readable repeats, and it is unscored only when none is readable. Each repeat is stored as its own verdict with its `repeat` index.

The cycle reports agreement across repeats next to agreement across models, per rubric and model:

- **calls**: calls with at least two readable repeats. Pairwise counts each order as its own call.
- **stable**: calls whose repeats all fall on the same side of the rubric's threshold, the rule agreement across models uses.
- **mean_spread**: the mean gap between a call's highest and lowest repeat, on the 0 to 1 scale.

A call whose repeats straddle the threshold is flagged `repeats_disagree`. An engine without repeats answers with one verdict, and the cycle then reports none.

## The judge against the code scorers

`decision_score` is a code signal when the run records decisions or uses it as its signal. The studio registers it as a rubric of the platform tenant with `POST /v1/evals/rubrics` at the start of a cycle, and asks the judge it for the journeys the code scored. `process_conformance` is a code signal on every sequence and is not asked of the judge: shown each branching step's shares and the option taken, judges still let one rare step decide the score (0.25 to 0.5 for declined applications the code scores 0.71 to 0.79), so the comparison measured arithmetic rather than the judge. The definitions are in `apps/api/app/judge_rubrics.py`: the brief and a question go in as the prompt and the rendered journey as the response, and the judge answers a 1 to 5 score that the engine normalises to 0 to 1.

The cycle's `agreement.code` gives, per rubric, the code's pass count and mean, and per model the judge's mean, how many journeys it agrees on, and the mean gap to the code's score. The judge's verdict passes at 0.75 for the decision score, whose code bar is every decision within 0.9 of the best. A journey where they differ is flagged `code_disagrees`. The code's verdict stays the signal: the rubric measures the judge, never decides acceptance, and an unreadable verdict on it does not reopen the run.

The cycle's `judging` records the repeats, their temperature, and each registered rubric's engine digest; each verdict of a registered rubric carries that `rubric_digest`. An engine without tenant rubrics still judges the other rubrics, and `judging.notes` says what was skipped.

## Controls: can the judges tell?

Agreement between judges, and across a judge's repeats, is perfect when every journey gets the top score, which is what two local judges did before this. So each cycle also asks the judges about journeys with one known defect: every sampled journey gets every flawed copy the pack confirms for it (`apps/api/app/controls.py`, found by `packages/sectors/src/sectors/controls.py`; decision 19), so a judge's discrimination rests on several controls per rubric, not one or two. Controls are asked once at temperature 0, as conformance is:

| Control | Defect | Confirmed by | Rubrics that should score it lower |
|---|---|---|---|
| `missing_step` | an inner event removed, searched from the middle outward | the pack's replay fails at a later event, and so do its hard checks | correctness, helpfulness |
| `worse_choice` | at an outcome decision, the rival with the lowest simulated chance of reaching the goal, at most 0.9 of the choice made; the journey ends there | the decision values, and the hard checks still pass | decision score |
| `slow_wait` | one wait stretched to ten times its step's longest wait, at least 30 days | the step's dwell and cycle ranges, and the hard checks still pass | helpfulness |
| `reversed` | every event in reverse order, at the original times | the pack's replay and hard checks fail | correctness |

A copy keeps its original's customer messages and narrates its own events with the pack's templates, so it reads like any other journey with one thing wrong. A code-comparison rubric is asked of a control only where it was asked of the original. One control, a missing step first, is also set against its original as a pairwise question, blind and in both orders: the judge should pick the original.

The cycle's `agreement.discrimination` gives, per rubric and model, how many controls scored below their original (the mean of their readable repeats against the original's) and the rate; below half, the judge is `blind` there and flagged `blind_to_defect`. `agreement.pairwise_control` gives each model's share of picks of the original, a tie counting a half; at 0.5 or below it is blind to that too. Each sampled journey's `controls` name what was changed. Each kind also has its own count, rate, and the mean scores of the originals and of their copies. The reversed copies are the cycle's `canary`, reported as before with how many journeys had one. Control verdicts carry `control` (the kind, or `pairwise:<kind>`, and the reversed ones `canary`), and stay out of scores, agreement, and flags about the run's journeys.

### Which rubrics decide

A rubric that cannot tell a flawed journey from a sound one should not decide whether a run is good (decision 18). Helpfulness and correctness decide acceptance only when the primary judge, in the same cycle, scored at least half of their controls lower than the originals, over at least three controls (`MIN_CONTROLS`), and for correctness also caught the reversed journeys. A rubric that does not keeps scoring and writing its revision notes, and is flagged `did_not_decide`. When neither decides, the run is accepted or not on the code's hard checks and safety alone, and the run page says the judges could not tell. A judge that could not be read has not shown that it cannot tell, so a sampled journey left unscored on any of the three still blocks acceptance, and the run can be judged again. `agreement.deciding` gives the primary judge, each rubric with whether it decided and why, and `code_only`.

The cycle also records which model served each named judge (`agreement.served_by`). When the two judges were served by one model, `agreement.same_model` names it, the cycle is flagged `same_model`, and the run page says their agreement is one model agreeing with itself.

The studio's questions name what to look for: helpfulness asks about skipped steps, events out of order, and waits far past a step's usual time, and is told each step's usual wait after the one before, from the pack's reference process, so it can see such a wait; it says a journey that ends in a failure is as representative as one that succeeds; correctness asks about a step that comes before what it depends on; the decision score asks about a choice clearly worse than another open one.

## Generated against real: can the judges tell?

Asked to score a journey on its own, the judges miss defects they catch every time when asked which of two journeys is sounder. So where a study has real journeys, each cycle also asks them to tell a generated journey from a real one (decision 22). A data source whose cases are journeys, an uploaded event log or the hotel and BTS catalogue sources, keeps a sample at calibration: up to 24 cases (`REAL_CASES`), drawn evenly with a fixed seed, as event types and hours since each case began and nothing else. They stay on the data source, are never returned by the API, and never reach an export.

Each sampled journey is set against one of those cases, blind and in both orders, asked once at temperature 0. Both are drawn alike (`render_steps`): the steps in order, each with the time since the first, and the generated journey cut to the events the study's sources record, timed from the first of them, so neither a step the data cannot see nor a time it never records gives the generated one away. The question says one was recorded from a real customer and asks for it, with a reason under 40 words.

The cycle's `agreement.realism` gives the sources, how many real cases they hold, and per judge how many verdicts it gave, how often it picked the real case (a tie counting half), and the reasons it gave. A judge that picks the real case in three of four or more is flagged `distinguishable`. Like the controls, the comparison decides nothing (decision 23): its verdicts carry `control: realism` and stay out of the journeys' scores and the controls' discrimination.

## Written turns: faithful to their facts?

Provider-written turns pass code checks for their events, amounts, identifiers, and language, and can still change what happened: live, a Turkish turn for a declined guarantee said the reservation was cancelled. So when a run has written turns, each cycle also reads a sample of them (`JUDGE_TEXT_SAMPLE`, 6 by default, at most 50; 0 reads none), taken in a seeded order, one per sequence before a second from any (`apps/api/app/faithfulness.py`). A large run is read batch by batch until the sample has enough candidates.

Each turn is asked of every judge with `turn_faithfulness`, which the studio registers with the engine like the code-comparison rubrics. The judge reads the written turn against the facts it had to state: its events in order and the template it replaced, with every amount and wait. The verdict is `{"faithful": boolean, "reason": string}`. A turn is faithful when it states each of those events with its meaning unchanged, keeps each amount and wait, and adds no outcome, cause, decision, or event the facts do not give; wording, tone, and speaking to the customer are free.

A turn that every readable repeat of every judge calls unfaithful goes back to its template in the stored run, in the database for a small run and in its batch file for a large one; its `written_by` and `template` are emptied, and its text flags are found again (decision 14). One faithful repeat keeps it: a false alarm costs a template sentence, not data. The cycle's `agreement.faithfulness` gives each turn's text, template, events, what each judge found, and a reason, and per model how many turns it called unfaithful; each judge that does so is flagged `unfaithful_turn`. `judging.faithfulness` lists the turns put back, and `generation.text.reverted` counts them for the run. Faithfulness verdicts carry the turn's `segment_id` and decide nothing about acceptance.

## Study rubrics

The judge can propose a solution and a behavior rubric for a study, after the code's solution and behavior rubrics:

- **What it reads.** From a run of the study, one group: a primary and its rollouts, with both outcomes when the group has them, up to four journeys. A run without groups gives four journeys taken across kinds and outcomes. The judge reads them with the study brief and its warm-start passages.
- **How it is asked.** Through `/v1/evals/run` with `rubric_proposal`, a rubric the studio registers for the platform tenant, so a reasoning judge answers without thinking, in JSON, inside a scheduler slot. Each rubric comes back as a title, a description, three to five checkable criteria, what a journey scoring 5, 3, and 1 shows, and the judge's `fit`: how clearly the group differs on those criteria. Text past the limits is trimmed.
- **Where it runs.** `POST /runs/{run_id}/rubric-proposals` starts a `propose_rubrics` job; demo accounts get `DEMO_RUBRIC_PROPOSALS_PER_DAY` (5). A proposal records the run, the journeys and their outcomes, the passages' sources, the judge, and its raw answer. A new proposal replaces the study's untouched proposals and keeps edited and approved ones.

The owner reviews them in the study rubrics panel under the judge panel, or with `GET`, `PATCH`, and `DELETE /projects/{project_id}/rubrics/{rubric_id}` and `POST .../approve`:

- An edit is checked: a title of up to 80 characters, a description, criteria, and anchors of up to 300 each, and two to six criteria. An edited rubric is proposed again until it is approved, so what the judge asks is always what was approved.
- Approving a rubric retires the study's approved rubric of the same kind.

An approved rubric is a declarative engine rubric. Its engine name is `study_<kind>_` followed by a hash of its content, so an edit makes a new rubric and every verdict's `rubric_digest` names the text that judged it. Each cycle of the study registers its approved rubrics and asks them of every sampled journey, with repeats. The cycle reports them in `agreement.code` next to the code's rubric of their kind (`signal`, `kind`, `title`), with agreement across models and across repeats. A study rubric's verdict passes at 0.5, a 3 of 5.

Study rubrics do not decide acceptance. A stratified sample holds failed journeys on purpose, and a rubric about the resulting state would reject good runs for them, as pairwise did. A run the judge has read can be judged again once the study approves a rubric its latest cycle did not ask; the run's `study_rubrics_pending` says so.

When the platform tenant holds the engine's limit of rubrics, registration removes the tenant's study rubrics that no study still approves, then tries once more.

The engine's typed errors reach the studio as explanations: a journey too long for the judge's context window names the tokens it needed and suggests lowering `JUDGE_PROMPT_TOKENS`, and a timeout gives the engine's limit.

Human notes are separate. A note targets the run, one trajectory, or one event, with stance `keep`, `revise`, or `drop`. Re-run copies the configuration, `parent_run_id`, and the selected note ids, then regenerates events from those notes and from any revision notes on the parent. A dropped event type is left out of the next bundle. A revised event type is delayed. A kept event type is retained when it still fits the length limit.

Provider deep search uses the selected account key to call that provider's web search, then stores a scrubbed report on the study. Named events in the report are kept on the next generation when they belong to the selected sub-domains of that sector. A drop note still removes an event the report named. The generator does not call the provider.
