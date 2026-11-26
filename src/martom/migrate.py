"""Plain-SQL migration runner.

Applies `migrations/NNN_name.sql` files in number order and records each applied file
(with a SHA-256 checksum) in the `schema_migrations` table, so a second run skips them.
Each file runs in its own transaction together with its bookkeeping row: a failing file
leaves no trace. An advisory lock stops two runners (e.g. two containers) racing.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

# Any fixed number works; every runner must use the same one.
ADVISORY_LOCK_KEY = 7_202_611

CREATE_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    """A migration file is invalid, was edited after it ran, or failed to apply."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Path) -> list[Migration]:
    """Read every `*.sql` file in `directory`, validated and sorted by number."""
    if not directory.is_dir():
        raise MigrationError(
            f"migrations directory not found: {directory} (run from the repo root or pass --dir)"
        )

    migrations: list[Migration] = []
    seen: dict[int, str] = {}
    for path in directory.glob("*.sql"):
        match = MIGRATION_NAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"bad migration file name {path.name!r}: expected NNN_lowercase_name.sql"
            )
        version = int(match.group(1))
        if version in seen:
            raise MigrationError(
                f"two migrations share number {version:03d}: {seen[version]} and {path.name}"
            )
        seen[version] = path.name
        migrations.append(Migration(version, path.name, path.read_text(encoding="utf-8")))

    return sorted(migrations, key=lambda m: m.version)


def apply_migrations(conn: psycopg.Connection, migrations: list[Migration]) -> list[str]:
    """Apply the migrations not yet recorded. Returns the names applied, in order.

    `conn` must be in autocommit mode: each migration opens its own transaction.
    """
    conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    try:
        conn.execute(CREATE_SCHEMA_MIGRATIONS)
        applied = dict(conn.execute("SELECT filename, checksum FROM schema_migrations"))

        newly_applied: list[str] = []
        for migration in migrations:
            if migration.name in applied:
                if applied[migration.name] != migration.checksum:
                    raise MigrationError(
                        f"{migration.name} was edited after it was applied; "
                        "add a new migration instead"
                    )
                continue
            try:
                with conn.transaction():
                    conn.execute(migration.sql)  # type: ignore[arg-type]  # trusted repo file
                    conn.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                        (migration.name, migration.checksum),
                    )
            except psycopg.Error as exc:
                raise MigrationError(f"{migration.name} failed: {exc}") from exc
            newly_applied.append(migration.name)
        return newly_applied
    finally:
        # On a dead connection the unlock would raise and hide the real error;
        # the server releases the lock when the session ends anyway.
        if not conn.broken:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


def run_migrations(database_url: str, directory: Path) -> list[str]:
    """Connect to `database_url` and apply pending migrations from `directory`."""
    migrations = discover_migrations(directory)
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            return apply_migrations(conn, migrations)
    except psycopg.Error as exc:
        # Connection, lock or schema_migrations bookkeeping errors.
        raise MigrationError(f"database error: {exc}") from exc
