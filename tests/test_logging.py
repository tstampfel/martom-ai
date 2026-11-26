"""Structured JSON logging: one JSON object per line, carrying the current request id."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest

from martom.log import JsonFormatter, configure_logging, request_id_var


def make_record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("martom.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formats_a_record_as_one_json_line() -> None:
    line = JsonFormatter().format(make_record("hello %s"))

    assert "\n" not in line
    entry = json.loads(line)
    assert entry["level"] == "INFO"
    assert entry["logger"] == "martom.test"
    assert entry["message"] == "hello %s"
    assert "timestamp" in entry
    assert entry["request_id"] is None


def test_includes_the_request_id_from_the_context() -> None:
    token = request_id_var.set("abc123")
    try:
        entry = json.loads(JsonFormatter().format(make_record("in a request")))
    finally:
        request_id_var.reset(token)

    assert entry["request_id"] == "abc123"


def test_includes_extra_fields() -> None:
    record = make_record("request finished", status_code=200, path="/health")

    entry = json.loads(JsonFormatter().format(record))

    assert entry["status_code"] == 200
    assert entry["path"] == "/health"


def test_includes_the_exception_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "martom.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    entry = json.loads(JsonFormatter().format(record))

    assert "RuntimeError: boom" in entry["exception"]


@pytest.fixture
def restore_loggers() -> Iterator[None]:
    # configure_logging changes the root and uvicorn loggers process-wide; put them back
    # so later tests don't depend on the order they run in.
    loggers = [
        logging.getLogger(name) for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    ]
    saved = [(lg.handlers[:], lg.level, lg.propagate, lg.disabled) for lg in loggers]
    yield
    for lg, (handlers, level, propagate, disabled) in zip(loggers, saved, strict=True):
        lg.handlers, lg.propagate, lg.disabled = handlers, propagate, disabled
        lg.setLevel(level)


@pytest.mark.usefixtures("restore_loggers")
def test_configure_logging_sets_level_and_json_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("WARNING")

    logging.getLogger("martom.test").info("hidden")
    logging.getLogger("martom.test").warning("shown")

    lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert [json.loads(line)["message"] for line in lines] == ["shown"]


def test_an_explicit_request_id_on_the_record_wins() -> None:
    # The request middleware passes it via `extra`, so its line keeps the id even when
    # the record is formatted after the request's context has ended.
    entry = json.loads(JsonFormatter().format(make_record("done", request_id="r-1")))

    assert entry["request_id"] == "r-1"


def test_drops_uvicorns_ansi_coloured_duplicate_message() -> None:
    record = make_record("Started server process", color_message="\x1b[36mStarted\x1b[0m")

    assert "color_message" not in json.loads(JsonFormatter().format(record))


def test_extra_fields_cannot_overwrite_the_core_fields() -> None:
    record = make_record("hi", level="fake", logger="fake", timestamp="fake")

    entry = json.loads(JsonFormatter().format(record))

    assert entry["level"] == "INFO"
    assert entry["logger"] == "martom.test"
    assert entry["timestamp"] != "fake"
    assert entry["message"] == "hi"
    # The clashing values are kept under a prefix instead of being lost.
    assert entry["extra_level"] == "fake"


def test_includes_the_stack_when_stack_info_is_requested() -> None:
    record = make_record("where am I", stack_info="Stack (most recent call last):\n  here")

    assert "most recent call last" in json.loads(JsonFormatter().format(record))["stack"]
