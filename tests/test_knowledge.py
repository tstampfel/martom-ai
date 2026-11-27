"""Reading the knowledge chunks in `knowledge/` (no database needed)."""

import re
from pathlib import Path

import pytest

from martom.knowledge import KnowledgeError, parse_chunk, read_chunks

REPO_KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"

VALID = """\
---
id: razmak-stupova
title: Razmak stupova
topic: posts
sources:
  - "fence-rules.md: Panel fence"
  - "Toni, 26.11.2026"
status: draft
---

# Razmak stupova

Stup svaka 2 m.
"""


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_front_matter_and_body(tmp_path: Path) -> None:
    chunk = parse_chunk(write(tmp_path, "02-razmak.md", VALID))

    assert chunk.id == "razmak-stupova"
    assert chunk.title == "Razmak stupova"
    assert chunk.topic == "posts"
    assert chunk.sources == ("fence-rules.md: Panel fence", "Toni, 26.11.2026")
    assert chunk.status == "draft"
    assert chunk.body == "# Razmak stupova\n\nStup svaka 2 m."
    assert chunk.source_path == "02-razmak.md"


def test_content_hash_changes_when_any_part_of_the_file_changes(tmp_path: Path) -> None:
    before = parse_chunk(write(tmp_path, "a.md", VALID))
    approved = parse_chunk(write(tmp_path, "b.md", VALID.replace("draft", "approved")))

    assert before.content_hash != approved.content_hash
    assert re.fullmatch(r"[0-9a-f]{64}", before.content_hash)


def test_file_without_front_matter_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", "# Samo tekst\n")

    with pytest.raises(KnowledgeError, match=r"x\.md.*front matter"):
        parse_chunk(path)


def test_unclosed_front_matter_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", "---\nid: a\n# no closing line\n")

    with pytest.raises(KnowledgeError, match="front matter"):
        parse_chunk(path)


@pytest.mark.parametrize("field", ["id", "title", "topic", "status", "sources"])
def test_missing_field_is_rejected(tmp_path: Path, field: str) -> None:
    lines = [line for line in VALID.splitlines() if not line.startswith(f"{field}:")]
    if field == "sources":
        lines = [line for line in lines if not line.startswith("  - ")]
    path = write(tmp_path, "x.md", "\n".join(lines) + "\n")

    with pytest.raises(KnowledgeError, match=field):
        parse_chunk(path)


def test_unknown_status_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", VALID.replace("status: draft", "status: done"))

    with pytest.raises(KnowledgeError, match="status"):
        parse_chunk(path)


def test_id_must_be_a_lowercase_slug(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", VALID.replace("id: razmak-stupova", "id: Razmak Stupova"))

    with pytest.raises(KnowledgeError, match="id"):
        parse_chunk(path)


@pytest.mark.parametrize("value", ["Toni", "[]", "[1, 2]", '[""]'])
def test_sources_must_be_a_non_empty_list_of_strings(tmp_path: Path, value: str) -> None:
    text = VALID.replace('  - "fence-rules.md: Panel fence"\n  - "Toni, 26.11.2026"\n', "")
    path = write(tmp_path, "x.md", text.replace("sources:", f"sources: {value}"))

    with pytest.raises(KnowledgeError, match="sources"):
        parse_chunk(path)


def test_empty_body_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", VALID.split("# Razmak")[0])

    with pytest.raises(KnowledgeError, match="empty"):
        parse_chunk(path)


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", VALID.replace("topic: posts", "topic: [posts"))

    with pytest.raises(KnowledgeError, match=r"x\.md"):
        parse_chunk(path)


def test_repeated_front_matter_key_is_rejected(tmp_path: Path) -> None:
    # `status` is the approval gate: a second `status:` must not silently win.
    path = write(tmp_path, "x.md", VALID.replace("topic: posts", "status: approved\ntopic: posts"))

    with pytest.raises(KnowledgeError, match=r"x\.md.*status"):
        parse_chunk(path)


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_text(VALID, encoding="utf-8-sig")  # "UTF-8 with BOM", as some editors save

    assert parse_chunk(path).id == "razmak-stupova"


def test_file_that_is_not_utf8_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_bytes(VALID.replace("Razmak", "Žica").encode("cp1250"))

    with pytest.raises(KnowledgeError, match=r"x\.md.*UTF-8"):
        parse_chunk(path)


def test_unreadable_file_is_rejected(tmp_path: Path) -> None:
    # e.g. an editor's dangling lock symlink like `.#07-montaza.md`
    path = tmp_path / ".#x.md"
    path.symlink_to(tmp_path / "missing.md")

    with pytest.raises(KnowledgeError, match=r"\.#x\.md.*cannot read"):
        parse_chunk(path)


def test_read_chunks_skips_readme_and_sorts_by_file_name(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "# Nacrt za Tonija\n")
    write(tmp_path, "02-b.md", VALID.replace("razmak-stupova", "b"))
    write(tmp_path, "01-a.md", VALID.replace("razmak-stupova", "a"))

    assert [chunk.id for chunk in read_chunks(tmp_path)] == ["a", "b"]


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    write(tmp_path, "01-a.md", VALID)
    write(tmp_path, "02-b.md", VALID)

    with pytest.raises(KnowledgeError, match=r"razmak-stupova.*01-a\.md.*02-b\.md"):
        read_chunks(tmp_path)


def test_folder_without_chunks_is_rejected(tmp_path: Path) -> None:
    # A wrong --dir must not look like "the knowledge base is empty" and wipe the table.
    write(tmp_path, "README.md", "# Nacrt\n")

    with pytest.raises(KnowledgeError, match="no knowledge chunks"):
        read_chunks(tmp_path)


def test_missing_folder_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeError, match="not found"):
        read_chunks(tmp_path / "missing")


def test_repo_knowledge_folder_is_valid() -> None:
    chunks = read_chunks(REPO_KNOWLEDGE)

    assert len(chunks) >= 10
    assert all(chunk.status in {"draft", "approved"} for chunk in chunks)
