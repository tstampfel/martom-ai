"""`martom` command-line entry point (installed by `[project.scripts]` in pyproject.toml)."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from martom.migrate import MigrationError, run_migrations

# The local docker-compose database (fake credentials, same as .env.example), used when
# DATABASE_URL is unset so `uv run martom migrate` works without an env prefix.
LOCAL_DATABASE_URL = "postgresql://martom:martom@localhost:5432/martom"


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

    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    raise SystemExit(main())
