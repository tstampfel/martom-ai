"""Without ANTHROPIC_API_KEY, the key-needing entry points print a one-line hint, not a traceback.

Both run in a child process (like a person typing the command), so the test sees exactly what
lands on stderr and the real exit code. The environment holds fake secret values that must
never be echoed back.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Fake values; the hint must never contain any of them.
FAKE_SECRETS = {
    "ADMIN_TOKEN": "fake-admin-token-0123456789abcdefghijklmnop",
    "ORDER_PHONE": "+38760000000",
    "LANGSMITH_API_KEY": "lsv2-fake-langsmith-key",
}

ENTRY_POINTS = {
    "martom extract": [
        sys.executable,
        "-c",
        "import sys; from martom.cli import main; sys.exit(main(['extract']))",
    ],
    "record_extraction_fixtures --record": [
        sys.executable,
        str(ROOT / "scripts" / "record_extraction_fixtures.py"),
        "--record",
    ],
}


def _run(command: list[str], key: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(FAKE_SECRETS, APP_ENV="test")
    if key is not None:
        env["ANTHROPIC_API_KEY"] = key
    return subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False
    )


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
@pytest.mark.parametrize("key", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_a_missing_key_prints_a_one_line_hint_and_exits_non_zero(
    entry_point: str, key: str | None
) -> None:
    result = _run(ENTRY_POINTS[entry_point], key)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    assert "ANTHROPIC_API_KEY" in lines[0]
    assert "uv run --env-file .env" in lines[0]


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_the_hint_never_contains_a_secret_value(entry_point: str) -> None:
    result = _run(ENTRY_POINTS[entry_point], None)

    output = result.stdout + result.stderr
    for value in FAKE_SECRETS.values():
        assert value not in output


def test_the_script_writes_no_fixture_when_the_key_is_missing() -> None:
    golden = ROOT / "tests" / "fixtures" / "extraction" / "golden.json"
    before = golden.stat().st_mtime_ns

    _run(ENTRY_POINTS["record_extraction_fixtures --record"], None)

    assert golden.stat().st_mtime_ns == before
