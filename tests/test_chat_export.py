"""`martom export-chats` without a database: targets, the repo refusal, the record (#158).

Every chat here is hand-written and fake (060 000 000 style digits, invented names and
streets): real customer chats are never committed, even scrubbed.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from martom.cli import main
from martom.evals.export import (
    ExportRequestError,
    LocalDirTarget,
    OutputInsideRepoError,
    S3Target,
    export_key,
    export_record,
    git_checkout_containing,
    parse_export_request,
    take_export_request,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORTED_AT = datetime(2026, 11, 28, 9, 30, tzinfo=UTC)
THREAD_ID = "a" * 43  # shaped like a server-issued id


# --- refusing the git checkout -----------------------------------------------------


@pytest.mark.parametrize(
    "inside",
    [REPO_ROOT, REPO_ROOT / "evals", REPO_ROOT / "data" / "exports", REPO_ROOT / "new" / "dir"],
)
def test_local_target_refuses_a_path_inside_the_repo(inside: Path) -> None:
    with pytest.raises(OutputInsideRepoError):
        LocalDirTarget(inside)


def test_local_target_refuses_any_git_checkout(tmp_path: Path) -> None:
    (tmp_path / "other-repo" / ".git").mkdir(parents=True)

    with pytest.raises(OutputInsideRepoError):
        LocalDirTarget(tmp_path / "other-repo" / "exports")


def test_a_git_worktree_file_counts_as_a_checkout(tmp_path: Path) -> None:
    # In a linked worktree (like this one) `.git` is a file, not a folder.
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: /somewhere\n")

    assert git_checkout_containing(tmp_path / "wt" / "out") == tmp_path / "wt"


def test_a_relative_path_is_resolved_before_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT / "tests")

    with pytest.raises(OutputInsideRepoError):
        LocalDirTarget(Path("..") / "evals")


def test_a_symlink_into_the_repo_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "looks-outside"
    link.symlink_to(REPO_ROOT / "evals", target_is_directory=True)

    with pytest.raises(OutputInsideRepoError):
        LocalDirTarget(link)


def test_local_target_writes_outside_the_repo_privately(tmp_path: Path) -> None:
    target = LocalDirTarget(tmp_path / "exports")

    target.write("chats/2026-11-28/abc.json", b"{}")

    written = tmp_path / "exports" / "chats" / "2026-11-28" / "abc.json"
    assert written.read_bytes() == b"{}"
    assert written.stat().st_mode & 0o777 == 0o600


def test_local_target_refuses_a_key_that_climbs_out(tmp_path: Path) -> None:
    target = LocalDirTarget(tmp_path / "exports")

    with pytest.raises(ValueError, match="key"):
        target.write("../escape.json", b"{}")


# --- S3 ----------------------------------------------------------------------------


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeS3:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.objects = dict(objects or {})

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        return {"Body": FakeBody(self.objects[kwargs["Key"]])}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects.pop(kwargs["Key"], None)
        return {}


def test_s3_target_puts_an_encrypted_json_object() -> None:
    client = FakeS3()

    S3Target(client, "martom-chat-export-000000000000").write("chats/x.json", b"{}")

    assert client.calls == [
        {
            "Bucket": "martom-chat-export-000000000000",
            "Key": "chats/x.json",
            "Body": b"{}",
            "ContentType": "application/json",
            "ServerSideEncryption": "AES256",
        }
    ]


# --- the record ----------------------------------------------------------------------


def test_record_holds_scrubbed_customer_and_advisor_text_only() -> None:
    messages = [
        SystemMessage("system prompt"),
        HumanMessage("Zovem se Pero Perić, trebam ogradu 20 m. Tel 061 000 001"),
        AIMessage("", tool_calls=[{"name": "search", "args": {}, "id": "t1"}]),
        ToolMessage('{"hits": []}', tool_call_id="t1"),
        AIMessage("Pero, preporučujem Panel 3D 153 cm."),
        HumanMessage("Dostava na Izmišljenu 12, Čitluk."),
    ]

    record = export_record(messages, thread_id=THREAD_ID, exported_at=EXPORTED_AT)

    assert record["messages"] == [
        {"role": "customer", "text": "Zovem se [ime], trebam ogradu 20 m. Tel [telefon]"},
        {"role": "advisor", "text": "[ime], preporučujem Panel 3D 153 cm."},
        {"role": "customer", "text": "Dostava na [adresa], Čitluk."},
    ]
    assert record["format"] == 1
    assert record["exported_at"] == "2026-11-28T09:30:00+00:00"


def test_record_never_holds_the_thread_id() -> None:
    record = export_record([HumanMessage("Panel")], thread_id=THREAD_ID, exported_at=EXPORTED_AT)

    assert THREAD_ID not in json.dumps(record)
    assert len(record["thread"]) == 12


def test_order_form_names_and_addresses_are_scrubbed() -> None:
    messages = [HumanMessage("Ana ovdje, Izmišljena 9 Čitluk, može dostava?")]

    record = export_record(
        messages,
        thread_id=THREAD_ID,
        exported_at=EXPORTED_AT,
        names=["Ana Anić"],
        addresses=["Izmišljena 9"],
    )

    assert record["messages"][0]["text"] == "[ime] ovdje, [adresa] Čitluk, može dostava?"


def test_key_is_dated_and_named_by_the_log_id() -> None:
    key = export_key(THREAD_ID, EXPORTED_AT)

    assert key.startswith("chats/2026-11-28/")
    assert THREAD_ID not in key


# --- the command (no database reached) -------------------------------------------------


def test_command_refuses_an_output_inside_the_repo(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["export-chats", THREAD_ID, "--out", str(REPO_ROOT / "evals")])

    assert code == 1
    assert "inside a git checkout" in capsys.readouterr().err
    assert not (REPO_ROOT / "evals" / "chats").exists()


def test_command_needs_a_bucket_or_an_out_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CHAT_EXPORT_BUCKET", raising=False)

    assert main(["export-chats", THREAD_ID]) == 1
    assert "CHAT_EXPORT_BUCKET" in capsys.readouterr().err


def test_command_rejects_a_malformed_thread_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["export-chats", "not-a-thread", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "thread id" in err
    assert "not-a-thread" not in err  # never echo an id
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize(
    ("intro", "advisor", "expected"),
    [
        (
            "Zovem se Ivan zanima me cijena za 20 metara ograde",
            "Ivan, za 20 metara trebate 9 panela, mreža 1 metar.",
            "[ime], za 20 metara trebate 9 panela, mreža 1 metar.",
        ),
        (
            "Ime: Marko Marić trebam panel 153 cm",
            "Marko, Panel 3D 153 cm je na stanju.",
            "[ime], Panel 3D 153 cm je na stanju.",
        ),
    ],
)
def test_introduced_names_never_blank_product_words(
    intro: str, advisor: str, expected: str
) -> None:
    # R1-2: only the name itself is scrubbed across the chat.
    record = export_record(
        [HumanMessage(intro), AIMessage(advisor)], thread_id=THREAD_ID, exported_at=EXPORTED_AT
    )

    assert record["messages"][1]["text"] == expected


# --- request objects (R1-5): ids and names never on a logged command line ---------------


def test_take_request_reads_and_deletes_the_object() -> None:
    body = json.dumps({"thread_ids": [THREAD_ID], "names": ["Ana Anić"], "addresses": []})
    client = FakeS3({"requests/r1.json": body.encode()})

    request = take_export_request(client, "bucket", "requests/r1.json")

    assert request.thread_ids == (THREAD_ID,)
    assert request.names == ("Ana Anić",)
    assert client.objects == {}


def test_take_request_deletes_a_malformed_object_too() -> None:
    client = FakeS3({"requests/bad.json": b"not json"})

    with pytest.raises(ExportRequestError):
        take_export_request(client, "bucket", "requests/bad.json")
    assert client.objects == {}


@pytest.mark.parametrize("key", ["chats/2026-11-28/x.json", "requests/../chats/x.json", "x.json"])
def test_take_request_refuses_keys_outside_requests(key: str) -> None:
    with pytest.raises(ExportRequestError):
        take_export_request(FakeS3({key: b"{}"}), "bucket", key)


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"thread_ids": []}',
        b'{"thread_ids": "abc"}',
        b'{"thread_ids": ["a"], "names": [1]}',
    ],
)
def test_malformed_requests_are_refused_without_quoting_them(body: bytes) -> None:
    with pytest.raises(ExportRequestError) as error:
        parse_export_request(body)
    assert "abc" not in str(error.value)


def test_command_needs_ids_or_a_request_not_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["export-chats", "--request", "requests/r.json", "--", THREAD_ID]) == 1
    assert main(["export-chats", "--out", str(tmp_path)]) == 1
    assert "one of the two" in capsys.readouterr().err


def test_command_reads_a_request_only_from_the_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CHAT_EXPORT_BUCKET", raising=False)

    assert main(["export-chats", "--request", "requests/r.json", "--out", str(tmp_path)]) == 1
    assert "CHAT_EXPORT_BUCKET" in capsys.readouterr().err


def test_command_rejects_malformed_ids_from_a_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import boto3

    client = FakeS3({"requests/r.json": b'{"thread_ids": ["not-a-thread"]}'})
    monkeypatch.setenv("CHAT_EXPORT_BUCKET", "bucket")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)

    assert main(["export-chats", "--request", "requests/r.json"]) == 1
    err = capsys.readouterr().err
    assert "thread id" in err and "not-a-thread" not in err
    assert client.objects == {}  # deleted even though nothing was exported


def test_a_thread_id_starting_with_a_dash_parses_after_double_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R1-3: token_urlsafe ids start with "-" about 1 time in 64.
    seen: dict[str, Any] = {}

    def fake_run(thread_ids: Any, **kwargs: Any) -> int:
        seen["ids"] = list(thread_ids)
        return 0

    monkeypatch.setattr("martom.cli.run_export_chats_command", fake_run)
    dash_id = "-" + "a" * 42

    assert main(["export-chats", "--out", str(tmp_path), "--name", "Ana", "--", dash_id]) == 0
    assert seen["ids"] == [dash_id]


def test_a_hyphenated_introduced_name_is_scrubbed_across_the_chat() -> None:
    record = export_record(
        [HumanMessage("Zovem se Ana-Marija Anić"), AIMessage("Ana-Marija, evo ponude.")],
        thread_id=THREAD_ID,
        exported_at=EXPORTED_AT,
    )

    assert "Ana" not in json.dumps(record, ensure_ascii=False)
    assert record["messages"][1]["text"] == "[ime], evo ponude."
