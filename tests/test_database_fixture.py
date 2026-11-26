"""Tests for the `database_url` fixture in conftest.py. They need no database.

`pytester` runs a small throwaway test session that uses a copy of our conftest.py,
so we can check what the fixture does when Postgres can't be reached.
"""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

CONFTEST = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")

# Port 1 refuses connections immediately; no database server is needed.
UNREACHABLE_URL = "postgresql://martom:martom@127.0.0.1:1/postgres"


@pytest.fixture
def session_using_database(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(
        """
        def test_needs_database(database_url):
            assert database_url
        """
    )
    return pytester


def test_unreachable_database_skips_locally(
    session_using_database: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("TEST_DATABASE_URL", UNREACHABLE_URL)

    result = session_using_database.runpytest("-rs")

    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(
        ["*cannot connect to Postgres at 127.0.0.1:1*docker compose up -d --wait*"]
    )


def test_unreachable_database_fails_in_ci(
    session_using_database: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("TEST_DATABASE_URL", UNREACHABLE_URL)

    result = session_using_database.runpytest()

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*cannot connect to Postgres at 127.0.0.1:1*"])
