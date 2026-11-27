# martom-ai

An agentic RAG advisor for **Željezarija Martom**, a hardware store in Čitluk (BiH).

It is fence-first: a customer describes what they want to fence, the advisor asks only what is missing, recommends products and builds a complete materials list with quantities and an estimated total. It also searches the whole store catalogue (~2,600 OLX listings: tools, fittings, wire, garden, household). The customer collects items in a cart and sends the order to the shop through WhatsApp.

Prices, product IDs and quantities always come from the database or the materials calculator, never from the language model.

> Status: early development (milestone M0, foundations). Nothing is deployed yet.

## Docs

- [`docs/PLAN.md`](docs/PLAN.md): goal, architecture, stack, data model, milestones and issues. The source of truth.
- [`docs/mockups/`](docs/mockups/): the customer screens, mobile and web.

## Stack

Python 3.12 with [uv](https://docs.astral.sh/uv/), FastAPI, LangGraph, Postgres 16 (full-text search + pgvector), Redis, Claude (Sonnet for the agent, Haiku for extraction and grading), Cohere Rerank, LangSmith. React + TypeScript (Vite) frontend on Cloudflare Pages. AWS (ECS Fargate, RDS, CloudFront) via Terraform. See [Tech stack and decisions](docs/PLAN.md#tech-stack-and-decisions).

## Run locally

Requirements: [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python 3.12 for you) and Docker.

```sh
uv sync                     # create .venv and install dependencies
cp .env.example .env        # local config; every value in the example is fake
docker compose up -d --wait # Postgres 16 + pgvector and Redis, healthy
uv run martom migrate       # apply database migrations (safe to re-run)
uv run martom api           # the API on http://127.0.0.1:8000 (see API below)
uv run pytest               # tests (database tests skip if Postgres can't be reached)
uv run ruff check           # lint
uv run ruff format --check  # formatting
```

`martom migrate` and the database tests use `DATABASE_URL` / `TEST_DATABASE_URL` when they are set, and otherwise the local compose database on port 5432 (the fake values in `.env.example`). The Redis tests likewise use `TEST_REDIS_URL` or the local compose Redis on port 6379. Every git worktree shares one compose stack (project name `martom`).

If ports 5432 or 6379 are already in use (e.g. a local Postgres), start compose with the port inline, `POSTGRES_PORT=5433 docker compose up -d --wait`, and pass the matching URLs: `DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run martom migrate` and `TEST_DATABASE_URL=postgresql://martom:martom@localhost:5433/postgres uv run pytest`.

### API keys (`.env`)

The app never reads `.env` by itself: config comes from environment variables only. To run a command that needs a real key, put the key in your local `.env` (copied from `.env.example`) and let uv pass the file to that one command:

```sh
uv run --env-file .env martom extract
uv run --env-file .env python scripts/record_extraction_fixtures.py --record
```

`--env-file` only fills variables that are not set already: a variable exported in your shell, or given inline (`ANTHROPIC_API_KEY=... uv run ...`), wins over the value in the file. Without `ANTHROPIC_API_KEY`, both commands above print a one-line hint and exit 1.

`.env` is git-ignored and never committed. AI agents working in this repo never read, source or pass `.env` (see [CLAUDE.md](CLAUDE.md)); only a person runs the commands above.

### API

```sh
uv run martom api           # FastAPI on http://127.0.0.1:8000 (add --reload while developing)
curl -i http://127.0.0.1:8000/health
```

`GET /health` checks Postgres and Redis and answers `200 {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}`, or `503` with `"error"` for whichever is down (the reason goes to the log only). `martom api --host 0.0.0.0 --port 8000` is the form for a container. The same app also runs as `uv run uvicorn --factory martom.api.app:create_app`, but then logs are uvicorn's plain text instead of JSON.

Configuration is read from environment variables only (no `.env` file; see [`.env.example`](.env.example)): `APP_ENV`, `LOG_LEVEL`, `CORS_ALLOWED_ORIGINS` (only `https://martom-savjetnik.pages.dev` and `http://localhost[:port]` are accepted), `DATABASE_URL`, `REDIS_URL`. Unset values default to the local compose stack. Logs are one JSON object per line on stderr, and every request gets an `X-Request-ID` (an incoming short, plain one is kept), which is in each log line written during that request.

The chat endpoint and the customer page arrive in later milestones (see the plan).

## Licence

[MIT](LICENSE)
