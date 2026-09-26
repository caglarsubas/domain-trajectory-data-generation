# Domain trajectory studio

A studio for configuring trajectory runs, inspecting generated journeys, leaving feedback, and running again. Banking and insurance each have a constrained generator on the same run schema. A warm study can run web search with the account's own provider key; the report is scrubbed before it is stored. The evaluation cycle judges a candidate through `llm_inference_engine`.

## Layout

- `apps/api` — FastAPI accounts, encrypted keys, runs, and the judge client
- `apps/web` — the studio interface
- `packages/trajectory_contract` — object-centric records and the Sample / Sequence / Context / Segment hierarchy
- `packages/sectors` — sector packs; `banking` and `insurance` are registered
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

Asking the judge queues an `evaluate` job; the run's `judge_job` reports its progress, rubric by rubric, and a failure keeps its reason there while the previous cycle stays. A deep search queues a `deep_search` job and answers with it; `GET /jobs/{id}` returns its progress and, when it succeeds, the stored corpus item, and `POST /jobs/{id}/cancel` stops a queued or running job. The account's key is decrypted only inside the job and is never written to it. Inline, both answer when they finish, with the same status codes as before.

## Accounts

Register as `user` or `demo`. Both must bring their own provider key. The admin account is created from `ADMIN_EMAIL` and `ADMIN_PASSWORD` and is the only account that can store platform keys. The judge uses `INFERENCE_ENGINE_API_KEY` from the environment, not a user key.

Saving or replacing a key checks it with a free authenticated call to the provider, listing its models. A key the provider rejects is refused and not stored, and a replacement it rejects leaves the old key in place. When the provider cannot be reached, the key is saved but not ready until `POST /credentials/{id}/check` succeeds; the Keys page shows each key's last check and can run it again.

Demo accounts have daily limits over a rolling 24 hours, set by `DEMO_RUNS_PER_DAY` (10), `DEMO_JUDGE_CYCLES_PER_DAY` (10), and `DEMO_DEEP_SEARCHES_PER_DAY` (3), and a run size limit, `DEMO_MAX_SEQUENCES` (2,000), which counts an accepted-group target at its ceiling of five times the target. A job that failed, or was cancelled before it started, does not count. Past a daily limit the API answers 429 with the time the next slot frees; `GET /quota` reports use, and the composer shows it.
