"""`martom` command-line entry point (installed by `[project.scripts]` in pyproject.toml)."""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg
import uvicorn
from pydantic import ValidationError

from martom.knowledge import KnowledgeError, run_load_knowledge
from martom.log import configure_logging, logging_config
from martom.migrate import MigrationError, run_migrations
from martom.olx.client import OlxClient
from martom.olx.job import RunKind, RunStats, apply_skipped_run, run_sync
from martom.settings import Settings

logger = logging.getLogger(__name__)

# The local docker-compose database (fake credentials, same as .env.example), used when
# DATABASE_URL is unset so `uv run martom migrate` works without an env prefix.
LOCAL_DATABASE_URL = "postgresql://martom:martom@localhost:5432/martom"

# A sync run sends no OLX request that would start later than this after it began; it
# ends as 'partial' instead and the next run picks up the rest (Tomislav, #8).
SYNC_RUN_DEADLINE_SECONDS = 3 * 60 * 60


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="martom")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate = commands.add_parser("migrate", help="apply pending SQL migrations")
    migrate.add_argument(
        "--dir",
        type=Path,
        default=Path("migrations"),
        help="folder with NNN_name.sql files (default: ./migrations, so run from the repo root)",
    )

    api = commands.add_parser("api", help="run the HTTP API (FastAPI on uvicorn)")
    api.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to listen on (default: 127.0.0.1; use 0.0.0.0 in a container)",
    )
    api.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    api.add_argument("--reload", action="store_true", help="restart on code changes (dev only)")

    sync = commands.add_parser(
        "sync", help="sync the shop's OLX listings into olx_listing and record a sync_run"
    )
    # Like a TS union of flags: at most one of --full / --apply-skipped may be given.
    sync_mode = sync.add_mutually_exclusive_group()
    sync_mode.add_argument(
        "--full",
        action="store_true",
        help="weekly full pass: fetch every listing's details, changed or not",
    )
    sync_mode.add_argument(
        "--apply-skipped",
        type=int,
        metavar="RUN_ID",
        help="re-check and deactivate the listings a run skipped because of the 10%% guard",
    )
    sync.add_argument(
        "--scheduled",
        action="store_true",
        help="set by the scheduler (M6-1): only scheduled runs count toward auto-apply",
    )

    load_knowledge = commands.add_parser(
        "load-knowledge", help="load the Markdown chunks in knowledge/ into knowledge_chunk"
    )
    load_knowledge.add_argument(
        "--dir",
        type=Path,
        default=Path("knowledge"),
        help="folder with the chunk files (default: ./knowledge, so run from the repo root)",
    )

    args = parser.parse_args(argv)

    if args.command == "load-knowledge":
        return run_load_knowledge_command(args.dir)

    if args.command == "sync":
        if args.scheduled and args.apply_skipped is not None:
            parser.error("--scheduled is for nightly and --full runs, not --apply-skipped")
        return run_sync_command(
            full=args.full, apply_skipped=args.apply_skipped, scheduled=args.scheduled
        )

    if args.command == "api":
        return run_api(args.host, args.port, reload=args.reload)

    if args.command == "migrate":
        # `or` also covers DATABASE_URL set to an empty string.
        database_url = os.environ.get("DATABASE_URL") or LOCAL_DATABASE_URL
        try:
            applied = run_migrations(database_url, args.dir)
        except MigrationError as exc:
            print(f"migration failed: {exc}", file=sys.stderr)
            return 1
        for name in applied:
            print(f"applied {name}")
        if not applied:
            print("nothing to apply")
    return 0


def run_load_knowledge_command(directory: Path) -> int:
    """`martom load-knowledge [--dir DIR]`: exit 0 when loaded, 1 when nothing was loaded."""
    database_url = os.environ.get("DATABASE_URL") or LOCAL_DATABASE_URL
    try:
        stats = run_load_knowledge(database_url, directory)
    except KnowledgeError as exc:
        print(f"load-knowledge failed: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"load-knowledge failed: database error: {exc}", file=sys.stderr)
        return 1
    print(
        f"knowledge_chunk: inserted {stats.inserted}, updated {stats.updated}, "
        f"unchanged {stats.unchanged}, deleted {stats.deleted}"
    )
    return 0


def make_olx_client(settings: Settings) -> OlxClient:
    """The OLX client for a sync run (a function so tests can swap in a fake transport)."""
    return OlxClient(
        base_url=settings.olx_api_base_url,
        shop_username=settings.olx_shop_username,
        min_request_interval_seconds=settings.olx_request_interval,
        run_deadline_seconds=SYNC_RUN_DEADLINE_SECONDS,
    )


def run_sync_command(
    *, full: bool = False, apply_skipped: int | None = None, scheduled: bool = False
) -> int:
    """`martom sync [--full | --apply-skipped RUN_ID] [--scheduled]`.

    Exit 0 for a clean run ('succeeded'), 1 when it broke ('failed'), 2 when it worked but
    was 'partial' (including a run OLX stopped early) or 'skipped_guard'. Any non-zero
    exit makes the scheduled task (M6-1) show as failed; `sync_run.status` says which
    case it was.
    """
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return 1
    configure_logging(settings.log_level)
    kind: RunKind = "apply_skipped" if apply_skipped is not None else "full" if full else "nightly"

    async def sync() -> RunStats:
        async with (
            await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as conn,
            make_olx_client(settings) as olx,
        ):
            if apply_skipped is not None:
                return await apply_skipped_run(conn, olx, apply_skipped)
            return await run_sync(conn, olx, kind=kind, scheduled=scheduled)

    try:
        stats = asyncio.run(sync())
    except Exception:
        logger.exception("sync run (%s) crashed", kind)
        return 1
    if stats.status == "succeeded":
        return 0
    return 1 if stats.status == "failed" else 2  # 2: partial or skipped_guard


def run_api(host: str, port: int, *, reload: bool) -> int:
    # Validate the settings here, so a bad env var is one clear line instead of a
    # traceback from inside uvicorn. create_app() reads them again in the server process.
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return 1
    # The app is passed as an import string with factory=True: uvicorn imports
    # martom.api.app and calls create_app() itself, which --reload needs.
    uvicorn.run(
        "martom.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_config=logging_config(settings.log_level),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
