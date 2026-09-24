# Domain trajectory studio

A studio for configuring trajectory runs, inspecting generated journeys, leaving feedback, and running again. Banking and insurance each have a constrained generator on the same run schema. A warm study can run web search with the account's own provider key; the report is scrubbed before it is stored. The evaluation cycle judges a candidate through `llm_inference_engine`.

## Layout

- `apps/api` — FastAPI accounts, encrypted keys, runs, and the judge client
- `apps/web` — the studio interface
- `packages/trajectory_contract` — object-centric records and the Sample / Sequence / Context / Segment hierarchy
- `packages/sectors` — sector packs; `banking` and `insurance` are registered
- `docs/banking` — the warm-start research reports

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

`docker compose up --build` starts Postgres, the API, and the studio. The API listens on port 8000 and the studio on port 3000. Tables are created when the API starts.

Compose fills local defaults when these are unset or empty in `.env`: `CREDENTIAL_MASTER_KEY`, `JWT_SECRET`, `ADMIN_EMAIL` (`admin@example.com`), and `ADMIN_PASSWORD` (`choose-a-password-123`). Put a real inference-engine key in `.env` when you want the judge to run. Do not commit `.env`.

```bash
docker compose up --build
```

Schema updates also live in `apps/api/alembic`. The API creates tables on startup.

## Accounts

Register as `user` or `demo`. Both must bring their own provider key. The admin account is created from `ADMIN_EMAIL` and `ADMIN_PASSWORD` and is the only account that can store platform keys. The judge uses `INFERENCE_ENGINE_API_KEY` from the environment, not a user key.
