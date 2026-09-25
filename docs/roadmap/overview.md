# Domain trajectory studio: roadmap overview

Repository: https://github.com/caglarsubas/domain-trajectory-data-generation

Last reviewed: 25 September 2026. `main` is at `8b00aa7` and is the only branch on the remote. Pull requests 1 through 6 are merged and their branches are deleted, so new work branches off `main`.

Companion files: [delivered.md](delivered.md) records what each merged slice established; [next-slice.md](next-slice.md) is the approved plan for the work now in front of us.

## Purpose

A platform where users generate comprehensive, complete, qualitative, representative domain-specific trajectory data for post-training, decision scoring, and evaluation of LLMs and Jev-type models.

Users upload warm-start documents, which may be deep-search reports, papers, repositories, or data sources. The platform recommends a warm start and states plainly that it produces better results, while still allowing a cold start. Users configure each run by expected data size, trajectory length, sub-domain scope, language, and reward and signal mechanisms. They inspect the result, leave feedback on the step that looks wrong, and start the next pass from that note.

## Standing constraints

These hold across every slice and should not be renegotiated silently.

- Sector order is banking, then insurance, then telecommunication, airways, and hotels. Adding a sector widens the `sector` literal and registers a pack. It does not change the run schema.
- The judge is the platform tenant on `llm_inference_engine`, tenant `domain-trajectory-data-generation`, organization `org-trajdata`. Local hard checks run first, and a failed check must not call the model. The rubrics are helpfulness, correctness, and safety, with pairwise quality only when an alternative branch exists. A cold start is marked `reference_quality=weak`.
- The admin account may store platform provider keys. User and demo accounts must bring their own key. Credentials are encrypted with `CREDENTIAL_MASTER_KEY` and are returned only as provider, label, and fingerprint.
- Secrets stay out of git, logs, and API responses. `.env` is gitignored. Provider keys and `INFERENCE_ENGINE_API_KEY` are never returned or logged, and tests mock HTTP rather than printing keys.
- A cold start requires `cold_start_acknowledged`. A warm start requires at least one corpus item.
- An alternative branch is a simulated alternative, not a causal counterfactual.
- Only assistant segments are trainable.
- The generator never calls a provider to write events. Deep search is a separate provider call whose scrubbed report is stored and then steers the existing generator.
- Jev-type is a target family on the same contract, not a trainer.
- The MiMo paper PDF is not committed.

## Stack

FastAPI, SQLAlchemy, and Alembic over Postgres with a SQLite fallback, Pydantic for the contract, and a Next.js App Router studio. Docker Compose runs Postgres, the API, and the studio together. The studio is published on host port 3000 and the API on host port 18000, which `API_HOST_PORT` overrides.

## Position today

Shipped and merged: the trajectory contract, accounts and key custody, warm and cold start, corpus upload and links, provider web search with a scrubbed report, banking and insurance generators on one run schema, the judge cycle with feedback and re-run, the studio interface, and the Docker Compose stack.

Three things still block the stated use of the data. Generated runs cannot leave the platform, every prompt group holds exactly one sequence, and the reward mechanisms offered in the composer are placeholders rather than implementations. Those three are the approved next slice.

## Backlog after the current slice

In the order I would take them.

1. Judge reliability. Repeat each rubric with different seeds, record agreement, and flag a passing candidate the judge scores low as a possible false positive and the reverse as a possible false negative. Today each rubric is judged once with a fixed seed.
2. Agentic tool segments. The contract already allows `role="tool"`, but no generator emits one, so journeys are narrated prose rather than interaction traces. This also opens the held-out harness axis for measuring generalization.
3. Corpus depth. `apps/api/app/corpus_text.py` drops binary files and caps the excerpt at two thousand characters, so an uploaded PDF contributes nothing today, and repository links are never fetched. The purpose statement names papers and repositories explicitly.
4. The remaining sectors: telecommunication, airways, and hotels. These come after the reward layer is shared, so each new pack inherits it rather than copying it.
5. Scale. A run is generated inside the request and the studio materializes at most sixty-four primary trajectories, while the schema accepts a target of up to one hundred thousand. Honouring the larger sizes needs a job queue with progress and resumability.
6. A decision-scoring rubric. `signal_mechanism` already offers `decision_score`, but no rubric implements it, so that choice currently changes only the prompt text.
