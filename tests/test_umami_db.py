"""Umami's database and role on the shared instance (#163 review r1: R1-1, R1-2, R1-3).

The database tests run the set-up as a NON-superuser "master" (LOGIN CREATEROLE CREATEDB,
owner of its app database), like the RDS master user (rds_superuser, not a superuser).
Roles are cluster-wide, so every name has a random suffix and is dropped afterwards.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from martom import db
from martom.umami_db import check_password, setup_statements, setup_umami_database

PASSWORD = "0123456789abcdef0123456789abcdef"
NEW_PASSWORD = "fedcba9876543210fedcba9876543210"


class RecordingConnection(psycopg.Connection[Any]):
    """A real connection that keeps the text of every statement it sends."""

    sent: list[str]

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        text = query.as_string(self) if isinstance(query, sql.Composable) else str(query)
        self.sent.append(text)
        return super().execute(query, params, **kwargs)


@dataclass
class Cluster:
    """A non-superuser master with its own app database, plus names for Umami's role/db."""

    admin_url: str  # the test server's superuser
    master_url: str
    app_database: str
    role: str
    database: str

    def url_as(self, user: str, password: str, dbname: str) -> str:
        return make_conninfo(self.admin_url, user=user, password=password, dbname=dbname)

    def query(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            return conn.execute(statement, params).fetchall()


@pytest.fixture
def cluster(database_url: str) -> Iterator[Cluster]:
    admin_url = make_conninfo(database_url, dbname="postgres")
    suffix = uuid.uuid4().hex[:10]
    master, app_db = f"master_{suffix}", f"app_{suffix}"
    role, database = f"umami_{suffix}", f"umami_{suffix}"
    master_password = uuid.uuid4().hex
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN CREATEROLE CREATEDB PASSWORD {}").format(
                sql.Identifier(master), sql.Literal(master_password)
            )
        )
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(app_db), sql.Identifier(master)
            )
        )
    try:
        yield Cluster(
            admin_url=admin_url,
            master_url=make_conninfo(
                admin_url, user=master, password=master_password, dbname=app_db
            ),
            app_database=app_db,
            role=role,
            database=database,
        )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in (database, app_db):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
            for name in (role, master):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def run_setup(cluster: Cluster, password: str) -> tuple[object, list[str]]:
    with RecordingConnection.connect(cluster.master_url, autocommit=True) as conn:
        conn.sent = []
        result = setup_umami_database(conn, password, role=cluster.role, database=cluster.database)
        return result, conn.sent


def can_connect(url: str) -> bool:
    try:
        with psycopg.connect(url, connect_timeout=5):
            return True
    except psycopg.OperationalError:
        return False


def test_setup_works_as_a_non_superuser_master(cluster: Cluster) -> None:
    result, _ = run_setup(cluster, PASSWORD)

    assert result.created_role and result.created_database
    # The role logs in to its own database and owns it, so Umami's migrations can run.
    umami_url = cluster.url_as(cluster.role, PASSWORD, cluster.database)
    with psycopg.connect(umami_url, autocommit=True) as conn:
        conn.execute("CREATE TABLE probe (id int)")
    # ... but not to the app database.
    assert not can_connect(cluster.url_as(cluster.role, PASSWORD, cluster.app_database))

    [(superuser, createdb, createrole, limit, stored)] = cluster.query(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolconnlimit, rolpassword"
        " FROM pg_authid WHERE rolname = %s",
        (cluster.role,),
    )
    assert (superuser, createdb, createrole) == (False, False, False)
    assert limit == db.UMAMI_CONNECTIONS
    assert stored.startswith("SCRAM-SHA-256$")


def test_public_has_no_privilege_on_umami_or_app_database(cluster: Cluster) -> None:
    run_setup(cluster, PASSWORD)
    # has_database_privilege also reads a NULL ACL (= the defaults: PUBLIC may CONNECT).
    [(umami_connect, umami_temp, app_connect)] = cluster.query(
        "SELECT has_database_privilege('public', %s, 'CONNECT'),"
        " has_database_privilege('public', %s, 'TEMPORARY'),"
        " has_database_privilege('public', %s, 'CONNECT')",
        (cluster.database, cluster.database, cluster.app_database),
    )
    assert (umami_connect, umami_temp, app_connect) == (False, False, False)


def test_the_password_never_appears_in_any_statement(cluster: Cluster) -> None:
    _, sent = run_setup(cluster, PASSWORD)
    assert sent
    assert all(PASSWORD not in text for text in sent)
    # It is sent as a SCRAM verifier instead (only the hash can reach a server log).
    assert any("SCRAM-SHA-256$" in text for text in sent)


def test_rerun_is_safe_and_applies_a_new_password(cluster: Cluster) -> None:
    run_setup(cluster, PASSWORD)
    result, _ = run_setup(cluster, NEW_PASSWORD)

    assert not result.created_role and not result.created_database
    assert can_connect(cluster.url_as(cluster.role, NEW_PASSWORD, cluster.database))
    assert not can_connect(cluster.url_as(cluster.role, PASSWORD, cluster.database))


def test_setup_statements_take_a_verifier_never_a_password() -> None:
    statements = setup_statements(
        role="umami",
        database="umami",
        app_database="martom",
        verifier="SCRAM-SHA-256$4096:abc$def:ghi",
        connection_limit=5,
        create_role=True,
        create_database=True,
    )
    text = " ".join(repr(statement) for statement in statements)
    assert "SCRAM-SHA-256$4096:abc$def:ghi" in text
    assert "SUPERUSER" not in text  # only a superuser may name that attribute (R1-1)


@pytest.mark.parametrize(
    "bad", ["", "short", "x" * 31, "has spaces in it 0123456789abcdef", "pä" * 20]
)
def test_check_password_refuses_weak_or_url_unsafe_values_without_echoing_them(bad: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        check_password(bad)
    if bad:
        assert bad not in str(excinfo.value)


def test_check_password_accepts_hex() -> None:
    check_password(PASSWORD)


def test_master_url_is_not_a_superuser(cluster: Cluster) -> None:
    params = conninfo_to_dict(cluster.master_url)
    [(superuser,)] = cluster.query(
        "SELECT rolsuper FROM pg_roles WHERE rolname = %s", (params["user"],)
    )
    assert superuser is False
