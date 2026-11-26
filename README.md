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
uv run pytest               # tests (database tests skip if Postgres can't be reached)
uv run ruff check           # lint
uv run ruff format --check  # formatting
```

`martom migrate` and the database tests use `DATABASE_URL` / `TEST_DATABASE_URL` when they are set, and otherwise the local compose database on port 5432 (the fake values in `.env.example`). Every git worktree shares one compose stack (project name `martom`).

If ports 5432 or 6379 are already in use (e.g. a local Postgres), start compose with the port inline, `POSTGRES_PORT=5433 docker compose up -d --wait`, and pass the matching URLs: `DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run martom migrate` and `TEST_DATABASE_URL=postgresql://martom:martom@localhost:5433/postgres uv run pytest`.

The API and frontend arrive in later milestones (see the plan).

## Licence

[MIT](LICENSE)
