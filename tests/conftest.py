"""Shared fixtures.

Database tests need a running Postgres (`docker compose up -d --wait`). They use the server
in TEST_DATABASE_URL, or the local docker-compose server when it is unset, so a plain
`uv run pytest` works. Each test gets its own throwaway database, created on that server
and dropped afterwards, so tests never touch the development database. If the server
can't be reached the tests are skipped locally, but fail in CI (where CI is set), so a
broken database setup can never turn into a quietly green run.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

# The local docker-compose server (fake credentials, same as .env.example).
LOCAL_TEST_DATABASE_URL = "postgresql://martom:martom@localhost:5432/postgres"


@pytest.fixture
def database_url() -> Iterator[str]:
    # `or` also covers TEST_DATABASE_URL set to an empty string.
    server_url = os.environ.get("TEST_DATABASE_URL") or LOCAL_TEST_DATABASE_URL

    try:
        admin = psycopg.connect(server_url, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        params = conninfo_to_dict(server_url)
        where = f"{params.get('host', 'localhost')}:{params.get('port', 5432)}"
        message = f"cannot connect to Postgres at {where} ({exc})"
        if os.environ.get("CI"):
            pytest.fail(f"{message}; in CI database tests must run, not be skipped")
        pytest.skip(
            f"{message}; start it with `docker compose up -d --wait` "
            "or point TEST_DATABASE_URL at a running server"
        )

    name = f"martom_test_{uuid.uuid4().hex[:12]}"
    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(server_url, dbname=name)
    finally:
        with psycopg.connect(server_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
