"""Structured JSON logging with a per-request id.

Every log line is one JSON object, so CloudWatch (and `jq` locally) can filter on fields.
The request id lives in a `ContextVar`: like React context, but per async task, so each
concurrent request sees its own value without passing it through every function call.
"""

import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Set by the request-id middleware in martom.api.app; None outside a request.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Fields not to copy into the JSON: those every LogRecord has, plus noise. Anything else
# was passed via `extra={...}` and is logged as its own field.
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn adds a copy of the message with ANSI colour codes
}

# Fields the formatter itself writes; an `extra` key with one of these names is prefixed.
_CORE_FIELDS = {"timestamp", "level", "logger", "message", "exception", "stack"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            # An id passed explicitly via `extra` wins over the context (the request
            # middleware does this, so its lines keep the id after the request ends).
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }
        for key, value in vars(record).items():
            if key in _STANDARD_ATTRS or key.startswith("_") or key == "request_id":
                continue
            # Never let an `extra` field replace a core one (CloudWatch filters on `level`).
            entry[f"extra_{key}" if key in _CORE_FIELDS else key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        # default=str: values json can't encode (e.g. a UUID) are logged as their text.
        return json.dumps(entry, default=str, ensure_ascii=False)


def logging_config(level: str) -> dict[str, Any]:
    """A `logging.config.dictConfig` dict: all logs, uvicorn's too, as JSON lines on stderr.

    `martom api` passes it to uvicorn as `log_config`, so uvicorn applies it in every
    process it starts (including the worker that `--reload` restarts).
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "martom.log.JsonFormatter"}},
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stderr",
            }
        },
        "root": {"handlers": ["stderr"], "level": level},
        "loggers": {
            # Flow into the root handler instead of uvicorn's own text format.
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            # Our middleware logs every request with its id; this would duplicate it.
            "uvicorn.access": {"handlers": [], "propagate": False},
        },
    }


def configure_logging(level: str) -> None:
    """Apply `logging_config(level)` in this process."""
    logging.config.dictConfig(logging_config(level))
