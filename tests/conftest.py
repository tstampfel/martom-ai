"""Shared fixtures.

Database tests need a running Postgres (`docker compose up -d db`) and TEST_DATABASE_URL.
Each test gets its own throwaway database, created on that server and dropped afterwards,
so tests never touch the development database. Without TEST_DATABASE_URL they are skipped.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def database_url() -> Iterator[str]:
    server_url = os.environ.get("TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("TEST_DATABASE_URL not set (start Postgres with `docker compose up -d db`)")

    name = f"martom_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(server_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(server_url, dbname=name)
    finally:
        with psycopg.connect(server_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
