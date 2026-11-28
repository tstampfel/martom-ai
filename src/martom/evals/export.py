"""Export chosen customer chats, scrubbed, to a private place only (#158).

Real customer chats are never committed to this public repo, even scrubbed (Tomislav,
28 Nov 2026). `martom export-chats` reads the given chat threads from the checkpointer,
passes every message through `scrub_personal_data` and writes one JSON file per thread
to one of two targets:

- `S3Target`: the private export bucket (`CHAT_EXPORT_BUCKET`, `infra/chat_export.tf`):
  EU region, no public access, encrypted, every object deleted after 90 days (the
  Privatnost page promises "Očišćeni primjeri razgovora: najduže 90 dana"). Production
  runs the command as a one-off ECS task, since the database is only reachable inside
  the VPC.
- `LocalDirTarget`: a folder on this machine, for a local database. It refuses any
  folder inside a git checkout (this repo or any other), so an export can't be committed
  by accident.

A record holds the customer's and the advisor's text only (no system prompt, no tool
calls or tool output), never the thread id: the id lets its holder continue the chat,
so the record and the object key carry only `thread_log_id`, the short hash the API logs.
The scrub has known misses (see `scrub.py`), so a human still reads an export before it
is used anywhere, and an eval case made from it is rewritten by hand and approved by
Tomislav or Toni before it goes into `evals/` (`evals/README.md`).
"""

import json
import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from martom.api.threads import thread_log_id
from martom.checkpointer import open_checkpointer
from martom.evals.scrub import introduced_names, scrub_personal_data

logger = logging.getLogger(__name__)

EXPORT_FORMAT = 1
KEY_PREFIX = "chats"


class OutputInsideRepoError(ValueError):
    """The output folder is inside a git checkout, where an export could be committed."""


class ExportTarget(Protocol):
    """Where an export goes: `write` stores `body` under `key` ("chats/<date>/<id>.json")."""

    def write(self, key: str, body: bytes) -> None: ...


def git_checkout_containing(path: Path) -> Path | None:
    """The git checkout `path` is in (the folder holding `.git`), or None.

    The path is resolved first (relative parts, `..` and symlinks), so a link that
    points into a repo counts as inside it. `.git` may be a folder or, in a linked
    worktree, a file.
    """
    resolved = path.resolve()
    for folder in (resolved, *resolved.parents):
        if (folder / ".git").exists():
            return folder
    return None


class LocalDirTarget:
    """A folder outside every git checkout; files are readable by their owner only."""

    def __init__(self, directory: Path) -> None:
        checkout = git_checkout_containing(directory)
        if checkout is not None:
            raise OutputInsideRepoError(
                f"{directory} is inside a git checkout ({checkout}); export chats only to a "
                "folder outside every repository, or to the private S3 bucket"
            )
        self.directory = directory.resolve()

    def write(self, key: str, body: bytes) -> None:
        path = (self.directory / key).resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError(f"export key {key!r} points outside the export folder")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Created with 0o600 from the start (os.open), so no other user can read it even
        # for a moment; like `fs.openSync(path, "w", 0o600)` in Node.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(body)
        path.chmod(0o600)  # an existing file keeps its old mode through O_TRUNC


class S3Client(Protocol):
    """The one boto3 S3 client method the export uses (tests pass a fake)."""

    def put_object(self, **kwargs: Any) -> Any: ...


class S3Target:
    """The private export bucket. Objects are encrypted with S3-managed keys (SSE-S3)."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def write(self, key: str, body: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )


def export_key(thread_id: str, exported_at: datetime) -> str:
    """ "chats/<UTC date>/<thread_log_id>.json": a re-export the same day overwrites."""
    return f"{KEY_PREFIX}/{exported_at.astimezone(UTC):%Y-%m-%d}/{thread_log_id(thread_id)}.json"


def _role(message: BaseMessage) -> str | None:
    if isinstance(message, HumanMessage):
        return "customer"
    if isinstance(message, AIMessage):
        return "advisor"
    return None  # system prompts and tool output are not the conversation


def export_record(
    messages: Sequence[BaseMessage],
    *,
    thread_id: str,
    exported_at: datetime,
    names: Iterable[str] = (),
    addresses: Iterable[str] = (),
) -> dict[str, Any]:
    """The scrubbed JSON record of one chat.

    `names` and `addresses` are the order form's values, when there is one. The names a
    customer introduces anywhere in the chat ("Zovem se Pero") are added, so the advisor's
    later "Pero, ..." is scrubbed too.
    """
    turns = [
        (role, message.text)
        for message in messages
        if (role := _role(message)) is not None and message.text.strip()
    ]
    known_names = list(names)
    for role, text in turns:
        if role == "customer":
            known_names += introduced_names(text)
    known_addresses = list(addresses)
    return {
        "format": EXPORT_FORMAT,
        "exported_at": exported_at.isoformat(),
        "thread": thread_log_id(thread_id),
        "messages": [
            {
                "role": role,
                "text": scrub_personal_data(text, names=known_names, addresses=known_addresses),
            }
            for role, text in turns
        ],
    }


@dataclass(frozen=True)
class ExportStats:
    exported: int
    missing: int  # thread ids with no checkpoint (never existed, or already purged)


async def export_chats(
    database_url: str,
    thread_ids: Sequence[str],
    target: ExportTarget,
    *,
    names: Iterable[str] = (),
    addresses: Iterable[str] = (),
    now: datetime | None = None,
) -> ExportStats:
    """Write each thread's latest state, scrubbed, to `target`; read-only on the database.

    Logs counts only, never a thread id or chat text.
    """
    exported_at = now or datetime.now(UTC)
    names, addresses = list(names), list(addresses)
    exported = missing = 0
    async with open_checkpointer(database_url) as saver:
        for thread_id in thread_ids:
            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            saved = await saver.aget_tuple(config)  # type: ignore[arg-type]
            if saved is None:
                missing += 1
                continue
            messages = saved.checkpoint["channel_values"].get("messages", [])
            record = export_record(
                messages,
                thread_id=thread_id,
                exported_at=exported_at,
                names=names,
                addresses=addresses,
            )
            body = json.dumps(record, ensure_ascii=False, indent=2).encode()
            target.write(export_key(thread_id, exported_at), body)
            exported += 1
    logger.info(
        "chat export finished",
        extra={"event": "chat_export", "exported_threads": exported, "missing_threads": missing},
    )
    return ExportStats(exported=exported, missing=missing)
