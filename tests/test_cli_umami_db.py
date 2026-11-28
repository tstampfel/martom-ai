"""`martom umami-db-setup` (#163): refuses a missing or weak password without echoing it."""

import pytest

from martom import cli


def test_missing_password_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("UMAMI_DB_PASSWORD", raising=False)

    assert cli.main(["umami-db-setup"]) == 1
    assert "UMAMI_DB_PASSWORD is not set" in capsys.readouterr().err


def test_weak_password_exits_1_without_printing_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UMAMI_DB_PASSWORD", "tooshort-secret")
    # Never reached: the password check runs before any connection is used.
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")

    assert cli.main(["umami-db-setup"]) == 1
    captured = capsys.readouterr()
    assert "tooshort-secret" not in captured.out + captured.err
    assert "at least 32" in captured.err
