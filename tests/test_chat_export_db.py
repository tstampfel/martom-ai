"""`martom export-chats` against a real Postgres (see conftest.database_url) (#158).

The chats are hand-written fakes saved with the library's own saver, so the export reads
the same rows the API writes. Real customer chats are never committed, even scrubbed.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from martom.api.threads import thread_log_id
from martom.checkpointer import open_checkpointer, setup_checkpointer
from martom.cli import main
from martom.evals.export import LocalDirTarget, S3Target, export_chats

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 28, 9, 30, tzinfo=UTC)
CHAT = "c" * 43
OTHER_CHAT = "d" * 43
UNKNOWN = "u" * 43


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def save_chat(saver: AsyncPostgresSaver, thread: str, messages: list[Any]) -> None:
    checkpoint = empty_checkpoint()
    checkpoint["ts"] = NOW.isoformat()
    checkpoint["channel_values"] = {"messages": messages}
    checkpoint["channel_versions"] = {"messages": "1"}
    config: dict[str, Any] = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    await saver.aput(config, checkpoint, {"source": "input", "step": 0}, {"messages": "1"})


async def seed(database_url: str) -> None:
    setup_checkpointer(database_url)
    async with open_checkpointer(database_url) as saver:
        await save_chat(
            saver,
            CHAT,
            [
                HumanMessage("Zovem se Pero, trebam ogradu. Moj broj je 061 000 001."),
                AIMessage("Pero, za 20 m preporučujem Panel 3D 153 cm."),
            ],
        )
        await save_chat(saver, OTHER_CHAT, [HumanMessage("Imate li kolica?")])


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["ServerSideEncryption"] == "AES256"
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}


async def test_exports_scrubbed_chats_to_s3(database_url: str) -> None:
    await seed(database_url)
    s3 = FakeS3()

    stats = await export_chats(
        database_url, [CHAT, OTHER_CHAT, UNKNOWN], S3Target(s3, "bucket"), now=NOW
    )

    assert (stats.exported, stats.missing) == (2, 1)
    record = json.loads(s3.objects[f"chats/2026-11-28/{thread_log_id(CHAT)}.json"])
    assert record["messages"] == [
        {"role": "customer", "text": "Zovem se [ime], trebam ogradu. Moj broj je [telefon]."},
        {"role": "advisor", "text": "[ime], za 20 m preporučujem Panel 3D 153 cm."},
    ]
    # The id lets its holder continue the chat: never in a key or a record.
    assert not any(CHAT in key or CHAT.encode() in body for key, body in s3.objects.items())


async def test_exports_to_a_folder_outside_the_repo(database_url: str, tmp_path: Path) -> None:
    await seed(database_url)

    await export_chats(database_url, [CHAT], LocalDirTarget(tmp_path), now=NOW)

    files = list(tmp_path.rglob("*.json"))
    assert [file.name for file in files] == [f"{thread_log_id(CHAT)}.json"]
    assert "061" not in files[0].read_text()


def test_command_writes_to_the_out_folder(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asyncio.run(seed(database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)

    code = main(["export-chats", CHAT, UNKNOWN, "--out", str(tmp_path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "exported 1 chats" in out
    assert "1 not found" in out
    assert CHAT not in out and UNKNOWN not in out
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_command_exports_the_threads_of_a_request_object(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import boto3

    asyncio.run(seed(database_url))

    class RequestS3(FakeS3):
        def __init__(self) -> None:
            super().__init__()
            self.requests = {"requests/r1.json": json.dumps({"thread_ids": [CHAT]}).encode()}

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            data = self.requests[kwargs["Key"]]
            return {"Body": type("Body", (), {"read": lambda _self: data})()}

        def delete_object(self, **kwargs: Any) -> dict[str, Any]:
            del self.requests[kwargs["Key"]]
            return {}

    s3 = RequestS3()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CHAT_EXPORT_BUCKET", "bucket")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: s3)

    assert main(["export-chats", "--request", "requests/r1.json"]) == 0

    [key] = s3.objects
    assert key.endswith(f"/{thread_log_id(CHAT)}.json")
    assert s3.requests == {}
    assert CHAT not in capsys.readouterr().out
