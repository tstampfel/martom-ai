"""Umami's own database and login role on the shared Postgres instance (#163).

Umami (self-hosted visit statistics, docs/analytics.md) keeps its tables in a separate
database, `umami`, owned by a separate login role, `umami`. Terraform can't reach the
private RDS instance, so `martom umami-db-setup` (a one-off ECS task, infra/umami.tf)
creates them. It runs as the RDS master user, which is NOT a Postgres superuser
(rds_superuser), so:

- it never names SUPERUSER/NOSUPERUSER (only a superuser may), and a role created by a
  non-superuser can't be a superuser anyway;
- in Postgres 16 the creator of a role gets only ADMIN on it, not SET, so before
  `CREATE DATABASE ... OWNER umami` it grants itself SET on the role (INHERIT FALSE, so
  the master never uses Umami's privileges by accident).

The password never appears in SQL text (review r1, R1-2): libpq hashes it on the client
into a SCRAM-SHA-256 verifier (`PQencryptPasswordConn`, psycopg's `encrypt_password`), and
only that verifier is sent. A failing statement that Postgres writes to its log (and RDS
exports to CloudWatch) can therefore show the hash at most, never the password. Nothing
here logs or prints the password, the verifier or a statement.

Re-running is safe: the role and database are created only when missing, the password,
the connection limit and the privileges are set again on every run.
"""

import re
from dataclasses import dataclass

import psycopg
from psycopg import sql

from martom.db import UMAMI_CONNECTIONS

UMAMI_ROLE = "umami"
UMAMI_DATABASE = "umami"

# The password also goes into Umami's DATABASE_URL by hand, so it must need no URL
# escaping: letters and digits only, at least 32 (`openssl rand -hex 32` gives 64).
_PASSWORD_PATTERN = re.compile(r"[A-Za-z0-9]{32,}")


@dataclass(frozen=True)
class UmamiDbSetup:
    created_role: bool
    created_database: bool


def check_password(password: str) -> None:
    """Refuse a password that is short or would need escaping in a URL.

    The message never contains the value.
    """
    if not _PASSWORD_PATTERN.fullmatch(password):
        raise ValueError(
            "UMAMI_DB_PASSWORD must be at least 32 letters and digits (openssl rand -hex 32)"
        )


def setup_statements(
    *,
    role: str,
    database: str,
    app_database: str,
    verifier: str,
    connection_limit: int,
    create_role: bool,
    create_database: bool,
) -> list[sql.Composed]:
    """The statements, in order. Takes the SCRAM verifier, never the password."""
    role_id, database_id = sql.Identifier(role), sql.Identifier(database)
    statements: list[sql.Composed] = []
    if create_role:
        statements.append(sql.SQL("CREATE ROLE {}").format(role_id))
    statements += [
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN NOCREATEDB NOCREATEROLE CONNECTION LIMIT {} PASSWORD {}"
        ).format(role_id, sql.Literal(connection_limit), sql.Literal(verifier)),
        # Postgres 16: lets the master SET ROLE umami (needed for OWNER below and for the
        # REVOKE as owner); a repeat grant only notices.
        sql.SQL("GRANT {} TO CURRENT_USER WITH SET TRUE, INHERIT FALSE").format(role_id),
    ]
    if create_database:
        statements.append(sql.SQL("CREATE DATABASE {} OWNER {}").format(database_id, role_id))
    statements += [
        # Only the owner (or a superuser) can revoke PUBLIC's default CONNECT and TEMP.
        sql.SQL("SET ROLE {}").format(role_id),
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database_id),
        sql.SQL("RESET ROLE").format(),
        # Umami's role must not reach the app database. The master owns it, so its own
        # grant stays (the API, jobs and migrate connect as the master).
        sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(app_database)),
    ]
    return statements


def setup_umami_database(
    conn: psycopg.Connection,
    password: str,
    *,
    role: str = UMAMI_ROLE,
    database: str = UMAMI_DATABASE,
    connection_limit: int = UMAMI_CONNECTIONS,
) -> UmamiDbSetup:
    """Create or update Umami's role and database. `conn` is the master user's
    autocommit connection to the app database (CREATE DATABASE can't run in a
    transaction)."""
    check_password(password)
    if not conn.autocommit:
        raise ValueError("setup_umami_database needs an autocommit connection")

    verifier = conn.pgconn.encrypt_password(
        password.encode(), role.encode(), b"scram-sha-256"
    ).decode()
    app_database = conn.execute("SELECT current_database()").fetchone()
    role_exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    database_exists = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
    ).fetchone()
    assert app_database is not None

    for statement in setup_statements(
        role=role,
        database=database,
        app_database=app_database[0],
        verifier=verifier,
        connection_limit=connection_limit,
        create_role=role_exists is None,
        create_database=database_exists is None,
    ):
        conn.execute(statement)
    return UmamiDbSetup(created_role=role_exists is None, created_database=database_exists is None)
