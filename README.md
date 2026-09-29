# Domain trajectory studio

A studio for configuring trajectory runs, inspecting generated journeys, leaving feedback, and running again. Banking, insurance, telecommunications, airlines, and hotels each have a constrained generator on the same run schema. A warm study can run web search with the account's own provider key; the report is scrubbed before it is stored. The evaluation cycle judges a candidate through `llm_inference_engine`.

## Layout

- `apps/api` — FastAPI accounts, encrypted keys, runs, and the judge client
- `apps/web` — the studio interface
- `packages/trajectory_contract` — object-centric records and the Sample / Sequence / Context / Segment hierarchy
- `packages/sectors` — sector packs; `banking`, `insurance`, `telecom`, `airline`, and `hotel` are registered, each after passing the gates in `sectors.gates`
- `docs/banking` — the warm-start research reports
- `docs/roadmap` — the purpose, the standing constraints, what shipped, and the next slice

## Run locally

```bash
python3 -m pip install -e ".[dev]"
cp .env.example .env
# Fill CREDENTIAL_MASTER_KEY, JWT_SECRET, and the inference-engine values.
# Do not commit .env.

export DATABASE_URL=sqlite:///./data/traj.db
export CREDENTIAL_MASTER_KEY=replace-me
export JWT_SECRET=replace-me-with-32-characters-minimum
export ADMIN_EMAIL=you@example.com
export ADMIN_PASSWORD=choose-a-password
export PYTHONPATH=packages/trajectory_contract/src:packages/sectors/src:apps/api
python3 -m uvicorn app.main:app --app-dir apps/api --reload --port 8000

cd apps/web && npm install && npm run dev
```

## CI

GitHub Actions runs the Python suite and the studio build on every pull request and on pushes to `main` (`.github/workflows/ci.yml`). It needs no secrets: the tests mock the inference engine, the providers, and the network.

## Docker Compose

`docker compose up --build` starts Postgres, the API, and the studio. Open the studio at http://localhost:3000. The API process listens on port 8000 inside the Compose network, and Compose publishes that on host port 18000 so it does not collide with another program already bound to 8000. Set `API_HOST_PORT` to choose a different host port. Tables are created when the API starts.

The studio calls the API through its own server, so sign-in works when the page is opened on a host other than localhost. Set `NEXT_PUBLIC_API_URL` only when the browser should call the API directly.

Compose fills local defaults when these are unset or empty in `.env`: `CREDENTIAL_MASTER_KEY`, `JWT_SECRET`, `ADMIN_EMAIL` (`admin@example.com`), and `ADMIN_PASSWORD` (`choose-a-password-123`). Put a real inference-engine key in `.env` when you want the judge to run. Do not commit `.env`.

```bash
docker compose up --build
```

Schema updates also live in `apps/api/alembic`. The API creates tables on startup and adds columns introduced since a table first shipped.

## Jobs

Generating, exporting, and judging a run are jobs, and so is a deep search. `JOBS_MODE` decides who runs them:

- `inline`, the default on SQLite: the request that creates the run generates it before answering.
- `thread`, the default on Postgres: a background thread in the API process picks jobs up.
- `worker`: a separate process runs them. Docker Compose starts one as the `worker` service; outside Compose, run `python -m app.worker` with the same environment as the API.

Runs of up to 64 sequences are one generator call stored on the run. Larger runs, up to 100,000 sequences, are generated in batches of 256 and written as compressed files under `DATA_DIR/runs/<run id>/` (default `data/runs`), with a checkpoint after every batch; a restarted worker resumes from the last finished batch. In Docker Compose the API and the worker share these files through the `traj_runs` volume. The studio reads a large run a journey at a time.

A large run is exported by a job too: `POST /runs/{id}/exports` (optionally with a held-out sub-domain) writes the four parts under `DATA_DIR/runs/<run id>/exports/`, the data parts gzipped, and the studio's Export panel prepares them, shows progress, and then offers the downloads. Small runs export directly.

A run's size can be a number of prompts drawn or, with more than one sequence per prompt, a number of accepted groups: the job keeps drawing, sizing each batch from the acceptance rate seen so far, until that many groups survive the dynamic sampler, and stops at five times the target if too few do. Shares per sub-domain split a run into parts with their own targets.

A run is `queued`, then `generating`, then `generated`, `failed`, or `cancelled`. The run page shows progress while it waits and can cancel it. A job whose worker stops sending heartbeats is requeued when a worker next starts.

`DELETE /runs/{id}` removes a run the account owns: its judge cycles and verdicts, its notes, and its files under `DATA_DIR/runs/<run id>/`. A run a job is still working on is refused with 409 until the job is cancelled. Its jobs stay without the run, so a demo account's daily quota still counts them, and runs made from it keep their journeys and lose only the comparison with it.

Asking the judge queues an `evaluate` job; the run's `judge_job` reports its progress, rubric by rubric, and a failure keeps its reason there while the previous cycle stays. A deep search queues a `deep_search` job and answers with it; `GET /jobs/{id}` returns its progress and, when it succeeds, the stored corpus item, and `POST /jobs/{id}/cancel` stops a queued or running job. The account's key is decrypted only inside the job and is never written to it. Inline, both answer when they finish, with the same status codes as before.

## Warm start

Uploaded documents are parsed once and the text is cached beside the file: PDF (with `pypdf`; scanned pages without a text layer are reported, not read), Word, HTML (the page's main content, without scripts, navigation, or forms), Markdown and plain text, and OpenAPI and AsyncAPI definitions in JSON or YAML, whose operations or channels are listed ahead of the raw text. Each document reports which parser read it, what it read, or why it could not.

A link is fetched by a `fetch` job when it is added. Only http and https are fetched, and every address the host resolves to must be public, checked again for each redirect and for the address actually connected to, so a link cannot reach the platform's own network. Bodies stop at 5 MB. A GitHub repository link (`repo` kind) is read through the GitHub API: its README, files under `docs/`, and any `openapi`, `swagger`, or `asyncapi` definitions, summarized, with their raw addresses listed as sources. `GITHUB_TOKEN` raises GitHub's rate limit; `FETCH_USER_AGENT` names the fetcher to the sites it reads. A link that cannot be fetched is kept and says why.

The study's documents are read into facts, each with its evidence sentences and a confidence. A fact is explicit when the text states it: a currency code in capitals, a dotted event name such as `kyc.passed`, a channel or product named in a sentence that does something with it. It is strongly implied when the text only points at it: a currency symbol or word, an event named in plain words, a product or channel mentioned in passing. Explicit facts steer the generator; implied ones wait in a review queue in the composer (`GET /projects/{id}/facts`, `POST /projects/{id}/facts/review`), and only accepted ones steer. Any fact can be set aside. The report also lists what a run takes from defaults because no fact covers it, and each run's steering report counts the facts it used and those still awaiting review.

Each run chooses a jurisdiction profile, `neutral` (the default), `tr` (Turkey), or `uk` (the United Kingdom). A profile sets the currency, which wins over the documents; the local product names, recorded on objects as `product_name`; the KYC rules and identity documents the judge's brief and the samples state; and the language the composer starts in. Neutral retail keeps the earlier behaviour: currency from the documents, then the language.

A data source calibrates the generator. Upload an event log as a `data_source` (CSV or Parquet with case, activity, and timestamp columns, XES, optionally gzipped, or OCEL 2.0) and a `calibrate` job maps its activities to the pack's events, by shared words unless a person corrects the mapping (`PUT /projects/{id}/corpus/{item}/mapping`), and counts how often each step follows another, and each pair of steps, and how long each takes. Runs then blend those next-step shares with the pack's priors in proportion to how much data backs them, taking them after the last two events wherever at least 25 observations back that pair and after the last event otherwise, and reading a journey's last events over the ones the data contains, so an event it never records does not hide the context and a journey it has not yet seen begins as its journeys do; draw durations around the observed quantiles once a step has five samples, and follow steps through events a run leaves out of scope. A step the data records right after an event it never contains, such as an approval after a KYC check BPI does not record, counts for that event, and a step the data repeats but a journey cannot, such as a second validation, is followed to the steps after it; the pack's machines still decide what is legal. The run's quality report measures representativeness against the data: fitness, precision, and the divergence of next-step shares among the events both share, after the last event and after the last two, where a conditional gap shows even when the next-step shares match. A run can opt out with `calibrate: false`.

`GET /catalogue` lists public sources. BPI Challenge 2017 (4TU.ResearchData General Terms of Use) and UCI Bank Marketing (CC BY 4.0) download on demand into the upload store (`POST /projects/{id}/catalogue`), never into the repository, and record their licence, origin, and snapshot date; BPI comes with its own activity mapping, and an activity it leaves out, such as an offer, stays unmapped unless a person maps it. The CFPB complaint database (CC0) is added from a CSV export, whose columns are recognized, because its search API no longer answers and its full file is over a gigabyte. HMDA and Freddie Mac wait on a terms review, and AMLSim and PaySim on the episode builder. Hotel studies can add the hotel booking demand datasets (Antonio, de Almeida, and Nunes, 2019, CC BY 4.0), whose bookings calibrate how often a confirmed reservation is changed, cancelled, or arrives, a cancelled deposit's fate, no-shows, lead times, time to cancellation, and stay length. Airline studies can add a month of BTS on-time performance (public domain), whose flights calibrate cancellations after check-in, delays at the gate between boarding and departure, delay lengths, late arrivals after a delay, and flight times; a calibrated run lands delayed flights late as often as the data does. A source sees only part of a journey, so an event it never contains keeps the pack's weight wherever it is a choice. The terms review of telecommunications and insurance sources listed both candidates without adding them: the FCC's consumer complaints are public domain but record no step after a complaint is filed, and the Texas Department of Insurance complaints carry no licence while TDI's site terms allow only unaltered, non-commercial copies, so they wait on TDI's written confirmation. Parquet needs the `parquet` extra (`pyarrow`), which the Docker image installs.

Steering reads every document in full. The judge's brief carries the passages most relevant to the study instead of the first 2,000 characters: documents are cut into passages on paragraph boundaries and ranked with BM25 against the study's sub-domains and event names, within `JUDGE_REFERENCE_CHARS` (6,000). Each cycle records which passages it used.

## Episodes

Post-training runs carry agent episodes (`episodes`, on by default when the consumer is post-training). Where a group's rollouts part ways, the builder takes the state the pack's machines had reached, the operations that could be called there, named after BIAN service domains and action terms (`CustomerOffer.Execute` with an `outcome` argument for approve or decline), a task in the run's language, and five rubric items a program checks: format, legality, grounding, decision, and report. When the study's documents include an OpenAPI definition, an operation whose words match, action included, carries its method, path, and `operationId`. Rollouts come from a scripted policy: the step the journey took, its legal alternatives, an operation the state forbids, and a call naming another case's object. Each is rewarded as MiMo does, verification times the rubric score, with group-relative advantages. The skeleton of legal operations, the case's objects, and the step taken is what provider rollouts are checked against.

Export adds `episodes.jsonl` and each rollout once per harness: `episodes-openai.jsonl` (chat messages with tool calls), `episodes-anthropic.jsonl` (tool use and result blocks), and `episodes-react.jsonl` (Thought, Action, Observation), which is held out of training to measure generalization. An episode takes its sample's split. The run page shows the focused journey's episode with each rollout's call, result, and rubric.

Provider rollouts (`provider_rollouts`, 0 to 4 per episode) let a model at the provider of the run's own key take each episode's turn: it sees the task and the operations, makes a call, the mock bank answers with the recorded result or a refusal, and the model reports what it did. That is two calls per rollout, sent only to that provider with the key in a header. Code checks every call against the skeleton: a known operation, arguments the schema accepts, a legal step, the case's own objects, and one call only, and scores it on the same rubric, so a provider rollout joins its group's advantages with policy `provider:<model>` and its `checks`. They need a key, episodes, and at least two sequences per prompt. The composer estimates the calls (prompts × rollouts × 2) and takes a cap (`provider_call_budget`, at most 4,000); failed calls count against it, and three failures stop the run's rollouts. A large run spends one budget across its batches and keeps it in the checkpoint. `generation.episodes.provider` reports calls, rollouts, skipped rollouts and why, errors, and how many calls were legal, grounded, or missing. The model defaults to `OPENAI_AGENT_MODEL` (gpt-5.5), `ANTHROPIC_AGENT_MODEL` (claude-sonnet-4-5), `GOOGLE_AGENT_MODEL` (gemini-2.5-flash), or `XAI_AGENT_MODEL` (grok-4.5); `provider_model` overrides it for a run. Demo runs make at most `DEMO_MAX_PROVIDER_CALLS` (100) calls.

Provider-written turn text (`provider_text`, off by default) lets a model at the provider of the run's own key write the assistant's turns instead of the pack's templates. After the journeys are drawn and before they are scored, each group's skeleton goes to the model in one call: the opening its sequences share, and for each sequence its follow-ups and, for each turn, its events in order, each with its template sentence, amount, and wait (since the previous event, or since it last happened). No object id or document text is sent. The model answers one sentence per event, labelled with the event, and code checks every turn: every event of the turn in order and nothing else, each amount stated, no other number than the skeleton gives, no identifier, email address, or link, no event name, and the run's language. A turn that fails keeps its template, as does one that copies its template word for word; a written turn carries `written_by: provider:<model>` and keeps the template it replaced in `template`. Journeys never change, and length penalties, turn flags, and the export's copy check see the written text. The composer estimates one call per prompt and takes a cap (`provider_text_budget`, at most 4,000), so the same cap covers up to 16 times as many sequences as a call per sequence; groups past it keep their templates, failed calls count, three failures stop the writing, and a large run spends one budget across its batches. `generation.text` reports the turns written, those that kept their template and why, the calls, and how many turns the judge later put back. Each judge cycle reads a sample of written turns (`JUDGE_TEXT_SAMPLE`, 6) against the facts each had to state, and a turn that every readable repeat of every judge calls unfaithful goes back to its template in the stored run (decision 14). Demo runs count these calls with provider rollouts against `DEMO_MAX_PROVIDER_CALLS`.

## Decision records

Decision-scoring runs and Jev-type targets record decisions (`decisions`, on by default for either). Each prompt's journey records the first decision of each outcome group, such as KYC passing or failing and an application being approved or declined, with what a model needs and nothing it cannot use:
- **Curated state.** The machines' states before the decision.
- **Derived facts.** Events so far, days since the start and since the previous step, money credited, debited, and net, and how often each event happened, precomputed because Jev-type models do not do arithmetic or date reasoning.
- **Options.** The outcomes that were open, each with the preconditions that make it legal, the generator policy's share of it, and its value: the share of 64 simulated continuations under the same policy that reach the pack's goal.
- **Outcome.** What the journey did next and whether it reached the goal.

Every counterfactual is relative to the generator's own policy (`counterfactual_basis: generator_policy`), never causal. Values run on their own random streams, seeded by the policy and the context, so recording decisions draws the same journeys, and one context gets one target across all of a run's batches.

Export turns each decision into typed questions in `decisions.jsonl`:
- a choice among the outcomes, whose target is the policy's shares
- true or false on whether the outcome taken, and one the state forbids, are allowed, decided by the machine rules
- a score for each outcome, whose target is its value

Every record has explicit criteria, an abstain answer that is never a target, and two variants that share its target and split: keys and options reordered, and the question paraphrased. A decision takes its sample's split as train, calibration (validation), or held out (test or a held-out sub-domain). `decision-record.schema.json` is the JSON Schema every record validates against, versioned with the contract (`decision-record/1`). `prefixes.jsonl` holds a record per trainable assistant turn, the conversation before it and the turn, for prefix-conditioned distillation. The manifest lists the parts each consumer uses, and the export panel marks the run's own. Consumer and target family no longer appear in the prompt text.

## Copies at export

Synthetic does not mean anonymous. At export, every 12-word run of each document the study holds, and of each data-source row long enough to have one (such as a complaint narrative), is indexed. Any exported record that repeats one of those runs is left out: a sample with its prefixes and journey task, an episode with its harness lines and agent task, or a decision record. Words are compared lowercased, whatever the punctuation. The manifest's `copies` section says how many documents and rows were indexed, whether the index hit its cap of a million runs, and what was left out, by part.

## Signals and evaluation

Every sequence is scored by five signal scorers (`sectors.scorers`):
- **outcome:** the journey reaches the pack's goal.
- **solution rubric:** the goal, no one-off decision left open at the end, and every selected sub-domain reached.
- **behavior rubric:** no step returns an object to a state it had left, and waits fall in the faster half of each step's range, which for a recurring step that has already happened, such as a monthly premium, is its cycle.
- **process conformance:** each step's policy share against the most likely step's, under the priors or the calibrated shares.
- **decision score:** at each first outcome decision of a group, the choice's simulated value against the best there.

The run's signal decides pass or fail, and so the rewards, advantages, and which groups are accepted. The solution and behavior rubrics are always the solution and behavior terms of MiMo's multiplicative reward (verification × solution × behavior). Choosing a signal draws the same journeys. Each sequence carries every signal's score, verdict, and items in `signals`, and `generation.rewards.signals` gives each signal's mean, pass rate, and pass@k over each prompt's group. The run page shows them in a table.

Evaluation runs build episodes by default, and choosing the evaluation consumer starts from groups of four. Export writes `tasks.jsonl`:
- **Journey tasks:** a task per prompt, with its opening, the group's sequences as reference trajectories (events and times), and each verifier's results over the attempts.
- **Agent tasks:** a task per episode, with the mock bank as its environment (state, operations, objects, legal events, and the answers to each step a journey took), the rubric's verifiers, the reference rollouts, and each policy's results.

`evaluation.json` reports avg@k and pass@k (the unbiased estimate from n attempts with c passes, 1 − C(n−c, k) / C(n, k)) by verifier for journeys and by policy for agent tasks. It also carries the pack's environment (events, preconditions, effects, milestones, goal) and the verifiers' definitions. With provider rollouts, each model's pass@k over its own attempts is a real evaluation of that model, and the run page shows it. The generator's pass@k is a reference for how hard each task is.

## Sector packs

A sector pack is a set of state machines and priors on the shared lifecycle engine, with its own objects, at least three phrasings of every event in English and Turkish, prompts, at least twenty customer openings and twenty follow-ups per language, a goal, a map from events to its industry's operations for episodes, and the episode agent's wording. Rewards, the quality report, calibration, episodes, decision records, and the five scorers work on every pack unchanged. Banking names its operations after BIAN, insurance after insurance capability domains, telecommunications after TM Forum Open API domains, airlines after IATA's NDC and ONE Order service areas, and hotels after HTNG and OpenTravel service areas.

A pack is registered, and so offered in the composer, only once it passes the gates in `sectors.gates` that banking passes, which the test suite runs for every registered pack:
- **complete_spec:** every event has roles, three plain phrasings per language, and an operation, and each language has twenty openings and twenty follow-ups.
- **legal_sweep:** 150 random configurations break no rule, repeat limit, or length bound.
- **every_sub_domain:** each sub-domain, alone and together, reaches its milestones.
- **group_signal:** each sub-domain alone yields accepted groups, with a pass and a fail, in at least a fifth of its groups of four.
- **diversity:** at least 32 distinct sequences in 64 journeys.
- **text_variety:** in each language, at least half the sentences distinct in 64 sequences narrated from templates alone.
- **reachable:** every event occurs in a large run.
- **stable:** a fixed seed reproduces the run.
- **agent_ready:** groups yield episodes, decision points, and all five signals.

Telecommunications (`telecom-semi-markov-v1`) covers:
- **Sales and ordering:** orders that may be abandoned, and a credit check that passes, asks for a deposit, or fails.
- **Activation and porting:** a SIM sent before the line goes live, and number ports that complete or fail before activation.
- **Billing and payments:** monthly bills paid or overdue, with suspension for an overdue bill and restoration once it is paid.
- **Plan changes:** changes that may be declined.
- **Fault management:** faults diagnosed and fixed remotely or by an engineer.
- **Retention:** cancellation requests that end in a retention offer, a cancellation, or a port-out.
- **Complaints:** complaints resolved or escalated to the dispute scheme.

Airlines (`airline-semi-markov-v1`) cover:
- **Shopping and booking:** a fare offer and an order that is paid and ticketed, fails payment, or expires.
- **Ancillaries and changes:** seats and bags, changes that may be declined, and voluntary cancellation with a refund.
- **Check-in and boarding:** a checked bag dropped before boarding, then boarded, a no-show, or denied boarding on an overbooked flight.
- **Disruption and compensation:** delays, cancellations, and denied boarding followed by a rebooking or a refund. A flight arrives on time, or more than three hours late only after a delay. Compensation claims follow a late arrival, a cancellation, or denied boarding, and are paid or rejected.
- **Baggage:** bags delivered, or delayed and returned.
- **Loyalty:** enrolment and miles credited after the flight.
- **Complaints:** complaints resolved or escalated.

Hotels (`hotel-semi-markov-v1`) cover:
- **Booking and reservations:** a reservation guaranteed and confirmed, declined, or lapsed.
- **Modifications and cancellations:** changes that may be declined before arrival, and cancellations refunded or charged a late fee.
- **Arrival and check-in:** a room assigned, then check-in, a no-show, or an oversold house walking the guest to another hotel with compensation.
- **In-stay services:** requests fulfilled or delayed, room issues fixed or solved by a move, and charges posted to the folio.
- **Check-out and billing:** the folio settled or disputed after check-out, and a dispute adjusted or rejected.
- **Loyalty and reviews:** enrolment, points after settlement, and reviews only once the stay has ended.
- **Complaints:** complaints resolved or escalated.

A journey ends at its natural length when the domain ends it, such as a declined application, or when it has reached a milestone of the run's scope and the scope has nothing more for it, such as a ticketed booking when only booking is selected. A journey that never reaches its scope, such as a flight with no bag in a baggage run, is redrawn. `GET /sectors/{id}/lengths` samples how long journeys in a scope can run, and the composer warns when most of them end before the requested minimum.

Every sub-domain has a failure its industry actually has, so its groups carry signal: insurance quotes can be abandoned, premiums missed until a policy lapses, renewals declined, and complaints rejected; telecom tickets can close without a fix; airline bags can arrive damaged or be lost and miles can go missing; a disputed hotel bill counts as a failed check-out. Priors that keep rare failures visible say so where they are set; a data source calibrates the real rates.

Jurisdiction profiles carry rules per sector in place of KYC: telecom runs are told about number porting and dispute schemes, airline runs about UK261 or SHY-Yolcu passenger rights, and hotel runs about guest registration (UK hotel records, Turkey's Kimlik Bildirim Sistemi) and price and cancellation disclosure.

## Judge

A cycle judges a sample of the run's journeys, six by default (`JUDGE_SAMPLE_SIZE`), taken from each kind of journey and outcome in turn, largest first, and chosen deterministically from the run and the cycle. The judge reads each journey with its objects, amounts and their direction, state changes, and the sample's text, shortened to fit `JUDGE_PROMPT_TOKENS` (8,000) when it must. Helpfulness, correctness, and safety are asked of the primary judge, `INFERENCE_ENGINE_JUDGE_MODEL` (`qwen3.8:27b`), and of the second opinion, `INFERENCE_ENGINE_SECOND_JUDGE_MODEL` (`gemma4:26b`, or empty for none); pairwise quality against the journey's alternative is asked in both orders. Each judge also gets one control journey, a sampled journey with its events put out of order, which the pack's replay rejects, and controls with one known defect: a step removed, a worse choice at a decision, and a wait stretched far past its range, each confirmed by the pack. The cycle reports how often each judge scores a control below its original, per rubric, flags a judge that does not as blind, and asks a pairwise question whose right answer is the original; controls decide nothing. Every call is repeated `JUDGE_REPEATS` times (3) at `JUDGE_TEMPERATURE` (0.7), and the cycle reports agreement across repeats next to agreement across models; a repeat that comes back as an empty JSON object is asked once more on a seed no repeat used. The judge also scores `decision_score`, which the studio registers with the engine as the platform tenant's rubric, and the cycle reports how often it agrees with the code scorer; it never decides acceptance. `process_conformance` is scored by code alone: given the next-step shares and the option each step took, judges still let one rare step decide the score, so asking them measured arithmetic rather than the judge. When the run has provider-written turns, the judges also read a sample of them with the registered `turn_faithfulness` rubric, and a turn every readable repeat calls unfaithful goes back to its template. From a group of a run's journeys, the judge can propose a study's own solution and behavior rubrics; the owner edits and approves them under the judge panel, and the study's cycles then register and ask them, next to the code's rubrics of the same kind, without letting them decide acceptance.

The cycle records each model's score per rubric, how often the two agree on pass or fail, whether each model kept its pairwise choice when the order was swapped, and audit flags in the spirit of MiMo's rollout auditing: a likely false positive when a judge passes the control journey, a likely false negative when it calls a legally replaying journey incorrect, a disagreement between the models, an order flip, and an unreadable verdict. Acceptance and revision notes follow the primary judge's helpfulness, correctness, and safety. Pairwise quality is scored and reported but decides nothing: a journey and its alternative both replay legally, so an unbiased judge sits near 0.5 and a 0.5 bar would reject about half of good runs. Both journeys are shown to the pairwise judge blind, without their kind, outcome, or which is the alternative, and the question says a failed or abandoned journey is as valid as a completed one. The run page shows the scores with the second opinion, the agreement, the flags, and each sampled journey's verdicts with their reasons, and opens a journey from there.

A run the judge has read is not judged again; only a cycle with an unreadable primary verdict can be. When the judge does not accept a run, `POST /runs/{id}/regenerate` makes a child run from the cycle's revision notes and the run's notes, and judges it as soon as it is generated. `max_cycles`, shown in the composer as judge rounds, limits how many rounds a study may go. Each run records which notes it applied and what each did, and `GET /runs/{id}/diff` compares a run with its parent: configuration, notes and their effects, data measures, events per 100 journeys, paths gained and lost, and the judge's scores. The run page shows it as "What changed".

Export follows the judge. An accepted run exports as it is; any other needs `allow_unaccepted=true` on the part or in the export job's body, and its manifest records `review.exported_without_acceptance` and a known limitation. A large run keeps an unaccepted export apart from an accepted one.

The engine judges at temperature 0, so asking one model the same question twice returns the same verdict; repeats wait on the engine, as do study-specific rubrics.

## Accounts

Register as `user` or `demo`. Both bring their own provider key for deep search; generating a run needs no key. The admin account is created from `ADMIN_EMAIL` and `ADMIN_PASSWORD` and is the only account that can store platform keys. The judge uses `INFERENCE_ENGINE_API_KEY` from the environment, not a user key.

Saving or replacing a key checks it with a free authenticated call to the provider, listing its models. A key the provider rejects is refused and not stored, and a replacement it rejects leaves the old key in place. When the provider cannot be reached, the key is saved but not ready until `POST /credentials/{id}/check` succeeds; the Keys page shows each key's last check and can run it again.

Demo accounts have daily limits over a rolling 24 hours, set by `DEMO_RUNS_PER_DAY` (10), `DEMO_JUDGE_CYCLES_PER_DAY` (10), and `DEMO_DEEP_SEARCHES_PER_DAY` (3), a run size limit, `DEMO_MAX_SEQUENCES` (2,000), which counts an accepted-group target at its ceiling of five times the target, and a provider call limit, `DEMO_MAX_PROVIDER_CALLS` (100). A job that failed, or was cancelled before it started, does not count. Past a daily limit the API answers 429 with the time the next slot frees; `GET /quota` reports use, and the composer shows it.
