"""`martom` command-line entry point (installed by `[project.scripts]` in pyproject.toml)."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from martom.migrate import MigrationError, run_migrations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="martom")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate = commands.add_parser("migrate", help="apply pending SQL migrations")
    migrate.add_argument(
        "--dir",
        type=Path,
        default=Path("migrations"),
        help="folder with NNN_name.sql files (default: ./migrations)",
    )

    args = parser.parse_args(argv)

    if args.command == "migrate":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            print("DATABASE_URL is not set", file=sys.stderr)
            return 2
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
