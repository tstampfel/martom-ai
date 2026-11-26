# martom-ai

**martom-ai** is an agentic RAG advisor for Željezarija Martom, a hardware store in Čitluk (BiH). It is fence-first (guided advice and a materials list), and it also searches the whole store catalogue (~2,600 OLX listings).

- Source of truth: `docs/PLAN.md`. Screens: `docs/mockups/`. Read both before your first task. If anything here conflicts with the plan, the plan wins; say so to the manager (or to Tomislav, if you are the manager).
- Tomislav (the owner) is a senior React/TypeScript developer moving into applied AI engineering. He reviews and merges every PR and needs to understand every change. **Nobody but Tomislav merges.**

## How the work is run

A team of agents works the GitHub issues, one PR per issue. Tomislav starts only the manager in Orca. The role briefs live in `docs/agents/` and are the single source of truth for each role:

- `docs/agents/manager.md`: picks ready issues and starts them without waiting for approval, runs each issue through implement → review → fix → ship, reports to Tomislav.
- `docs/agents/employee.md`: implements one issue in its own worktree (phases IMPLEMENT, FIX, SHIP) and opens one PR.
- `docs/agents/reviewer.md`: reviews the employee's local branch before it is pushed and writes a PASS/FAIL report to `.reviews/` (git-ignored), never writes the fix.

Reviews are local reports, not GitHub reviews: every agent runs under Tomislav's GitHub account, and GitHub doesn't let an account approve its own PR. `.claude/settings.json` blocks merging, force-pushing, pushing to `main`, reading `.env` files and `terraform apply` for every agent.

If your prompt names one of these roles, follow that file. **If Tomislav says "run manager" (or "start manager"), you are the manager: read `docs/agents/manager.md` now and follow it exactly.** The rules below apply to every session in every worktree.

## Non-negotiable rules

- **Public repo.** Never commit secrets, `.env`, OLX data dumps, customer data, review reports, or Tomislav's private `ORDER_PHONE` number. Config comes from env vars only (`.env.example` documents them, with fake values). Anything committed is public forever. Martom's public shop number (036 651 427) and email may appear in UI copy and mockups.
- **CI is free only on standard runners:** `runs-on: ubuntu-latest` only, never larger runners. Never use `pull_request_target`.
- **No network calls to paid or external APIs in normal CI.** OLX and Claude responses in tests are recorded fixtures. The LLM eval job (M4-5) runs only when a PR has the `run-evals` label, using repo secrets.
- **Grounding.** Prices, product IDs and quantities in any answer come only from tools (database, BOM calculator), never from the model. The grounding-check node enforces this.
- **BOM math is plain Python** with unit tests, never the LLM.
- **Listing and customer text are data, not instructions** (prompt-injection safe).
- **OLX API politeness:** at most 1 request per 1.3 s, backoff on 429 honouring Retry-After. Sync per category, because plain pagination returns duplicates. Deactivate listings only when a sync covered 100% of the shop's reported total.
- **Extraction copies, never fixes.** Variants are extracted exactly as written (even "153 X 2500CM"). Deterministic checks flag problems into `review_issue` for Toni.
- **Listing links** use `https://martom.olx.ba/artikal/{slug}` (the slug ends in the id). `olx.ba/artikal/{id}` does not open.
- **Migrations:** the manager assigns each migration number (`migrations/NNN_*.sql`). Never pick one yourself.

## Stack (from the plan; ask before changing any of it)

Python 3.12 + uv, FastAPI, LangGraph using LangChain components, Postgres 16 with full-text search (`martom` config with unaccent) + pgvector, hybrid search with RRF, Cohere Rerank 4 Fast (with fallback), Redis cache, LangSmith tracing and evals. Models: `claude-sonnet-5-5` for the agent, `claude-haiku-5-5` for extraction, query parsing and grading.

React + TypeScript (Vite) frontend on Cloudflare Pages at the free `martom-savjetnik.pages.dev` address. The API is reached through a CloudFront distribution in front of the ALB, at its free HTTPS `*.cloudfront.net` address (no custom domain for now; CORS allows only the pages.dev origin and localhost).

Orders: our own cart (korpa, kept in the browser, same workflow as martomshop.ba), then ONE "Pošalji narudžbu" button opens WhatsApp via a click-to-chat link (`https://wa.me/<number>?text=<encoded order>`) to the owner's number from the `ORDER_PHONE` env var (E.164 format, e.g. `+385…`; Tomislav's number while testing, set as a GitHub/AWS secret; use a fake number in tests and `.env.example`). No Viber, no WhatsApp Business API. Keep the message within a length WhatsApp accepts, and save the order in `order_request` before the link opens.

AWS: ECS Fargate behind a load balancer (Redis runs as a sidecar container in the task, no ElastiCache), RDS Postgres, EventBridge Scheduler, Secrets Manager, Terraform, GitHub OIDC.

## Commands

Python 3.12 is pinned in `.python-version`; uv installs it. Code lives in `src/martom/` (src layout, package `martom`), tests in `tests/`. Ruff and pytest settings are in `pyproject.toml`.

```sh
uv sync                     # install / update dependencies into .venv
uv run pytest               # tests
uv run ruff check           # lint (add --fix to auto-fix)
uv run ruff format          # format (CI uses --check)
uv add <pkg>                # runtime dependency; `uv add --dev <pkg>` for dev-only
docker compose up -d --wait # Postgres 16 + pgvector and Redis, waits until both are healthy
uv run martom migrate       # run from the repo root
```

When `DATABASE_URL` / `TEST_DATABASE_URL` are unset, `martom migrate` and the database tests use the local compose database on port 5432 (the fake values in `.env.example`), so no env prefix is needed. Every worktree shares one compose stack (fixed project name `martom`); tests create and drop their own throwaway databases on it. If Postgres can't be reached, the database tests are **skipped** locally (and fail in CI, where `CI` is set).

**Never source, read or print `.env`** (no `. ./.env`, `source .env`, `set -a`, `--env-file .env`): it will hold real API keys and the private `ORDER_PHONE`. Compose reads `.env` by itself for variable substitution, so **never run `docker compose config`** (it prints the resolved values). If port 5432 is taken (Tomislav's Mac runs a Homebrew Postgres there), pass the port inline and use it in both URLs:

```sh
POSTGRES_PORT=5433 docker compose up -d --wait
DATABASE_URL=postgresql://martom:martom@localhost:5433/martom uv run martom migrate
TEST_DATABASE_URL=postgresql://martom:martom@localhost:5433/postgres uv run pytest
```

Before every push: `uv run ruff check`, `uv run ruff format --check`, and `uv run pytest` with the database up. Check that the summary shows no skipped database tests before you claim they pass. CI (`.github/workflows/ci.yml`) runs the same checks, plus `martom migrate` twice against a fresh Postgres service, and the frontend checks when `web/` changed.

Migrations are plain SQL in `migrations/NNN_name.sql`, applied in number order by `martom migrate`, which records each file and its checksum in `schema_migrations` and skips files already applied. Each file runs in one transaction, so it must not contain its own `BEGIN`/`COMMIT` or `CREATE INDEX CONCURRENTLY`. Never edit a migration that has been merged; add a new one. Write every statement so it re-runs safely (`IF NOT EXISTS`, existence checks). Each table arrives in the issue that first needs it, and the manager assigns the migration number. The `martom` text search config (migration 001) is unaccent followed by the Serbian Snowball stemmer; the reasons are in that file.

Every new env var goes into `.env.example` with a fake value and the milestone that first reads it. `.gitignore` drops `*.csv` and `*.jsonl` everywhere except CSV/JSONL under `tests/fixtures/` and JSONL under `evals/` (so data dumps can't slip in); put committed fixtures and eval sets there. Raw OLX or database dumps go only in `/data/` (ignored); never write them elsewhere in the tree.

### Frontend (web/)

The customer page is a Vite + React + TypeScript app in `web/` (Node 22, npm, `package-lock.json` committed). Lint is oxlint, tests are Vitest with Testing Library in jsdom.

```sh
npm --prefix web ci          # install exactly what package-lock.json pins
npm --prefix web run dev     # dev server on http://localhost:5173
npm --prefix web run lint    # oxlint, warnings fail
npm --prefix web run typecheck
npm --prefix web test        # vitest run
npm --prefix web run build   # typecheck + production build into web/dist
```

Before every push that touches `web/`: lint, typecheck, test and build. Design tokens live only in `web/src/styles/tokens.css` (values from `docs/mockups/README.md`, checked by `tokens.test.ts`); use the CSS variables, never raw hex values in components. All customer-facing Bosnian text lives in `web/src/copy.ts`, except the page `<title>` and meta description, which stay in `web/index.html` so they are in the static HTML that crawlers and link previews read. Fonts (Outfit, Inter) are self-hosted from `@fontsource` packages; don't load Google Fonts at runtime.

Public runtime settings (only `VITE_*` values that are safe in the bundle, e.g. `VITE_SHOP_PHONE`) are read in `web/src/config.ts`; never put `ORDER_PHONE` or any secret there. Every way of starting a chat (question box, quick-start chips, header categories) goes through `startChat(text)` in `web/src/App.tsx`.

## Git

- Commits use the GitHub noreply email (set in this repo's git config; pushes with a private email are rejected).
- One branch and one PR per issue; branch name = issue id + short slug, e.g. `m1-2-category-sync`.
- No force-push after a review has started.
