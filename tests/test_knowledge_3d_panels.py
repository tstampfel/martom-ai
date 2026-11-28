"""The 3D-panel chunks (#169) against Toni's Q&A in `knowledge/sources/` (no database needed).

Each chunk made from the Q&A names its entries in `sources` ("p3d-042"). These tests check that
every usable entry went into exactly one chunk, that no entry Toni still has to check
(`needs_check`) did, and that no chunk states a price (prices come only from tools).
"""

import json
import re
from pathlib import Path

import pytest

from martom.knowledge import read_chunks

REPO = Path(__file__).resolve().parents[1]
QA_FILE = REPO / "knowledge" / "sources" / "martom_3d_panels_qa.jsonl"
QA_ID = re.compile(r"\bp3d-\d{3}\b")

# Entries without needs_check that are still left out of the chunks, with the reason.
# Empty since Tomislav cleared p3d-065 (28 Nov), which p3d-096 had waited on.
LEFT_OUT: dict[str, str] = {}


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

    assert len(flagged) == 2
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


# A currency word (also glued to a number: "49,90KM"), the euro sign, or ",-". "KM" and "Km"
# are the currency; lowercase "km" is kilometres ("u krugu oko 40 km", Toni's montage radius).
PRICE = re.compile(
    r"(?<![A-Za-z])(K[Mm]|BAM|EUR)(?![A-Za-z])|(?i:(?<![a-z])(maraka|marka|eura?)(?![a-z]))|€|,-"
)


def test_no_chunk_states_a_price() -> None:
    # Prices come only from tools (the grounding rule), in every chunk, not only the 3D ones.
    for chunk in read_chunks(REPO / "knowledge"):
        assert not PRICE.search(chunk.title + "\n" + chunk.body), chunk.id


@pytest.mark.parametrize(
    "text", ["49,90KM", "50 KM", "12 Km", "35,-", "20 €", "10 maraka", "5 eura"]
)
def test_price_pattern_catches_common_spellings(text: str) -> None:
    assert PRICE.search(text)


@pytest.mark.parametrize(
    "text", ["anker ploča 120x80 mm", "Kamion", "BAMBUS", "europaleta", "u krugu oko 40 km"]
)
def test_price_pattern_ignores_other_words(text: str) -> None:
    assert not PRICE.search(text)


def test_every_anchor_mention_names_its_type_and_bit() -> None:
    # Tomislav, 28 Nov: search may return any chunk alone, so a chunk that names an M8 anchor
    # also names the anchor type (tiplo anker / anker vijak) and the drill bit.
    for chunk in read_chunks(REPO / "knowledge"):
        body = chunk.body.casefold()
        # "M8x80" or "M8" + multiplication sign + "80"
        if re.search(r"m8\s*[x\u00d7]\s*\d+", body):
            assert "tiplo anker" in body or "anker vijak" in body, chunk.id
            assert "svrdlo" in body, chunk.id
