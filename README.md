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

Generating a run is a job. `JOBS_MODE` decides who runs it:

- `inline`, the default on SQLite: the request that creates the run generates it before answering.
- `thread`, the default on Postgres: a background thread in the API process picks jobs up.
- `worker`: a separate process runs them. Docker Compose starts one as the `worker` service; outside Compose, run `python -m app.worker` with the same environment as the API.

Runs of up to 64 sequences are one generator call stored on the run. Larger runs, up to 100,000 sequences, are generated in batches of 256 and written as compressed files under `DATA_DIR/runs/<run id>/` (default `data/runs`), with a checkpoint after every batch; a restarted worker resumes from the last finished batch. In Docker Compose the API and the worker share these files through the `traj_runs` volume. The studio reads a large run a journey at a time.

A run is `queued`, then `generating`, then `generated`, `failed`, or `cancelled`. The run page shows progress while it waits and can cancel it. A job whose worker stops sending heartbeats is requeued when a worker next starts.

## Accounts

Register as `user` or `demo`. Both must bring their own provider key. The admin account is created from `ADMIN_EMAIL` and `ADMIN_PASSWORD` and is the only account that can store platform keys. The judge uses `INFERENCE_ENGINE_API_KEY` from the environment, not a user key.
