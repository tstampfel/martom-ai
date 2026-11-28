"""The 3D-panel chunks (#169) against Toni's Q&A in `knowledge/sources/` (no database needed).

Each chunk made from the Q&A names its entries in `sources` ("p3d-042"). These tests check that
every usable entry went into exactly one chunk, that no entry Toni still has to check
(`needs_check`) did, and that no chunk states a price (prices come only from tools).
"""

import json
import re
from pathlib import Path

from martom.knowledge import read_chunks

REPO = Path(__file__).resolve().parents[1]
QA_FILE = REPO / "knowledge" / "sources" / "martom_3d_panels_qa.jsonl"
QA_ID = re.compile(r"\bp3d-\d{3}\b")

# Entries without needs_check that are still left out of the chunks, with the reason.
LEFT_OUT = {
    # "Dostava je besplatna kad mi radimo ugradnju": the same fact as p3d-065, which Toni
    # still has to check (installation terms), so it waits with it.
    "p3d-096": "free delivery with installation, flagged in p3d-065",
}


def qa_rows() -> list[dict]:
    lines = QA_FILE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def cited_ids() -> dict[str, list[str]]:
    """Q&A id -> the chunk ids whose sources cite it."""
    cited: dict[str, list[str]] = {}
    for chunk in read_chunks(REPO / "knowledge"):
        for qa_id in sorted({m for source in chunk.sources for m in QA_ID.findall(source)}):
            cited.setdefault(qa_id, []).append(chunk.id)
    return cited


def test_source_file_has_toni_s_101_entries() -> None:
    rows = qa_rows()

    assert len(rows) == 101
    assert len({row["id"] for row in rows}) == 101


def test_no_needs_check_entry_is_in_a_chunk() -> None:
    flagged = {row["id"] for row in qa_rows() if row.get("needs_check")}

    assert len(flagged) == 15
    assert flagged.isdisjoint(cited_ids())


def test_every_other_entry_is_in_exactly_one_chunk() -> None:
    usable = {row["id"] for row in qa_rows() if not row.get("needs_check")} - LEFT_OUT.keys()
    cited = cited_ids()

    assert set(cited) == usable
    assert {qa_id: chunks for qa_id, chunks in cited.items() if len(chunks) > 1} == {}


def test_left_out_entries_exist_and_are_not_flagged() -> None:
    rows = {row["id"]: row for row in qa_rows()}

    for qa_id in LEFT_OUT:
        assert qa_id in rows
        assert not rows[qa_id].get("needs_check")


def test_3d_chunks_are_approved_and_state_no_price() -> None:
    chunks = [c for c in read_chunks(REPO / "knowledge") if c.id.startswith("3d-panel-")]

    assert len(chunks) >= 8
    for chunk in chunks:
        assert chunk.status == "approved", chunk.id
        assert any("Toni, Q&A 28 Nov 2026" in source for source in chunk.sources), chunk.id
        # No amount of money: no currency word or sign anywhere in the chunk.
        assert not re.search(r"\b(KM|BAM|EUR|maraka|marka)\b|€", chunk.body), chunk.id
