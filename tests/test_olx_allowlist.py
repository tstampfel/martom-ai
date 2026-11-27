"""The allowlist guard's plain parts (#45): the committed file and the seen/moved check.

The sync job side (GET for a missing id, `sync_run.allowlist_warnings`) is tested in
`test_olx_job.py`, against a fake OLX and a real Postgres.
"""

import logging
from pathlib import Path

import pytest

from martom.olx.allowlist import (
    DEFAULT_PATH,
    Allowlist,
    AllowlistedListing,
    AllowlistWarning,
    check_seen,
    load_allowlist,
)

POST = AllowlistedListing(id=81645615, category_id=876, name="Stup sa stopom 40x40 mm")
TENSIONER = AllowlistedListing(id=18129143, category_id=1126, name="Španer")
ALLOWLIST = Allowlist(listings=(POST, TENSIONER), covered_categories={881: "Ograde"})


def row(listing_id: int, category_id: int) -> dict[str, object]:
    return {"id": listing_id, "category_id": category_id, "top_category_id": "224"}


# --- the committed file --------------------------------------------------------------------


def test_committed_allowlist_holds_the_three_fence_listings() -> None:
    allowlist = load_allowlist()

    assert {entry.id: entry.category_id for entry in allowlist.listings} == {
        81645615: 876,  # foot-plate post, allowlisted in "Oprema/pribor za stolariju"
        18129143: 1126,  # tensioner (španer)
        48475613: 1188,  # wire holder
    }
    assert allowlist.covered_categories == {881: "Ograde"}
    assert all(entry.name for entry in allowlist.listings)


def test_committed_allowlist_lives_next_to_the_sync() -> None:
    assert DEFAULT_PATH.name == "allowlist.toml"
    assert DEFAULT_PATH.parent.name == "olx"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "allowlist.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[covered_categories]\n881 = "Ograde"\n'
        '[[listing]]\nid = 1\ncategory_id = 2\nname = "a"\n'
        '[[listing]]\nid = 1\ncategory_id = 3\nname = "b"\n',
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_allowlist(path)


@pytest.mark.parametrize(
    "entry",
    [
        'id = "1"\ncategory_id = 2\nname = "a"',  # id as a string
        'id = 1\nname = "a"',  # no category
        "id = 1\ncategory_id = 2",  # no name
        'id = true\ncategory_id = 2\nname = "a"',  # a bool is not an id
    ],
)
def test_malformed_entry_is_rejected(tmp_path: Path, entry: str) -> None:
    path = write(tmp_path, f'[covered_categories]\n881 = "Ograde"\n[[listing]]\n{entry}\n')
    with pytest.raises(ValueError):
        load_allowlist(path)


def test_file_without_covered_categories_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, '[[listing]]\nid = 1\ncategory_id = 2\nname = "a"\n')
    with pytest.raises(ValueError, match="covered_categories"):
        load_allowlist(path)


@pytest.mark.parametrize("table", ['abc = "Ograde"', '881 = ""', "881 = 5"])
def test_malformed_covered_category_is_rejected(tmp_path: Path, table: str) -> None:
    path = write(
        tmp_path,
        f'[covered_categories]\n{table}\n[[listing]]\nid = 1\ncategory_id = 2\nname = "a"\n',
    )
    with pytest.raises(ValueError, match="covered_categories"):
        load_allowlist(path)


# --- check_seen -------------------------------------------------------------------------------


def test_listings_in_their_recorded_category_raise_nothing() -> None:
    warnings, missing = check_seen(
        ALLOWLIST, {81645615: row(81645615, 876), 18129143: row(18129143, 1126)}
    )

    assert warnings == []
    assert missing == []


def test_listing_in_another_uncovered_category_warns_with_old_and_new_category() -> None:
    warnings, missing = check_seen(
        ALLOWLIST, {81645615: row(81645615, 876), 18129143: row(18129143, 2091)}
    )

    assert warnings == [
        AllowlistWarning(
            listing_id=18129143,
            name="Španer",
            reason="moved",
            expected_category_id=1126,
            category_id=2091,
        )
    ]
    assert missing == []


def test_listing_moved_into_a_covered_category_is_logged_not_warned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="martom.olx.allowlist")

    warnings, missing = check_seen(
        ALLOWLIST, {81645615: row(81645615, 881), 18129143: row(18129143, 1126)}
    )

    assert warnings == []
    assert missing == []
    record = next(r for r in caplog.records if "81645615" in r.getMessage())
    assert record.levelno == logging.INFO
    # The wording Tomislav asked for (27 Nov 2026), with the id and the category name.
    assert (
        record.getMessage() == "81645615 now found via category Ograde; allowlist entry redundant"
    )
    assert record.event == "allowlist_listing_in_covered_category"  # type: ignore[attr-defined]


def test_listing_not_seen_is_returned_as_missing() -> None:
    warnings, missing = check_seen(ALLOWLIST, {18129143: row(18129143, 1126)})

    assert warnings == []
    assert missing == [81645615]


def test_category_sent_as_a_string_is_compared_as_a_number() -> None:
    warnings, _ = check_seen(
        ALLOWLIST,
        {81645615: {"id": 81645615, "category_id": "876"}, 18129143: row(18129143, 1126)},
    )

    assert warnings == []


def test_warning_as_json_keeps_every_field() -> None:
    warning = AllowlistWarning(
        listing_id=1, name="a", reason="404", expected_category_id=876, category_id=None
    )

    assert warning.as_json() == {
        "listing_id": 1,
        "name": "a",
        "reason": "404",
        "expected_category_id": 876,
        "category_id": None,
        "error": None,
    }
