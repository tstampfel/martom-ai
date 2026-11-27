"""The reviewed synonym list (src/martom/search/synonyms.md) that keyword search expands
queries with."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import martom.search.synonyms as synonyms_module
from martom.search.synonyms import (
    DEFAULT_PATH,
    QuerySynonyms,
    SynonymListError,
    default_query_synonyms,
    load_query_synonyms,
)

SECTION = "## Proširenja upita"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "synonyms.md"
    path.write_text(f"# Sinonimi\n\nUvod koji se ne čita.\n- ovo: nije u sekciji\n\n{body}")
    return path


def test_reads_one_rule_per_line_of_its_section(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        f"{SECTION}\n\n- kolica: tačke, kariola\n- Španer: zatezač\n\n## Kasnije\n\n- x: y\n",
    )

    synonyms = load_query_synonyms(path)

    assert synonyms == QuerySynonyms(
        rules=(("kolica", ("tačke", "kariola")), ("Španer", ("zatezač",)))
    )


def test_several_words_on_the_left_share_the_synonyms(tmp_path: Path) -> None:
    # "stupovi" stems to "stupov", not "stup", so a plural needs its own word (review R1-3).
    path = write(tmp_path, f"{SECTION}\n- stup, stupovi: kolac\n")

    assert load_query_synonyms(path).rules == (("stup", ("kolac",)), ("stupovi", ("kolac",)))


def test_from_words_and_to_words_are_parallel_lists_for_sql() -> None:
    synonyms = QuerySynonyms(rules=(("kolica", ("tačke", "kariola")), ("španer", ("zatezač",))))

    assert synonyms.from_words == ["kolica", "španer"]
    assert synonyms.to_words == ["tačke kariola", "zatezač"]


def test_a_comment_in_the_section_is_ignored(tmp_path: Path) -> None:
    path = write(tmp_path, f"{SECTION}\n<!-- - a: b -->\n- kolica: tačke\n")

    assert load_query_synonyms(path).from_words == ["kolica"]


def test_a_multi_line_comment_disables_the_rules_inside_it(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        f"{SECTION}\n- kolica: tačke\n<!--\n# stari\n- stup: kolac\n- kolac: stup -->\n"
        "- španer: zatezač\n",
    )

    assert load_query_synonyms(path).from_words == ["kolica", "španer"]


def test_a_line_after_a_multi_line_comment_keeps_its_line_number(tmp_path: Path) -> None:
    path = write(tmp_path, f"{SECTION}\n<!--\nkomentar\n-->\n* kolica: tačke\n")

    with pytest.raises(SynonymListError, match="line 10"):
        load_query_synonyms(path)


@pytest.mark.parametrize(
    "line",
    [
        "- ručna kolica: tačke",  # a phrase on the left
        "- kolica: ručne tačke",  # a phrase on the right
        "- kolica: ručna-kolica",  # split into two words by search
        "- ručna/kolica: tačke",
        "- kolica:",  # no synonyms
        "- kolica tačke",  # no colon
        "* kolica: tačke",  # another bullet
        "kolica: tačke",  # no bullet
        "neki tekst",  # prose inside the section
    ],
)
def test_a_malformed_line_is_an_error_naming_its_line(tmp_path: Path, line: str) -> None:
    path = write(tmp_path, f"{SECTION}\n- španer: zatezač\n{line}\n")

    with pytest.raises(SynonymListError, match="line 8"):
        load_query_synonyms(path)


def test_a_word_listed_twice_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, f"{SECTION}\n- kolica: tačke\n- Kolica: kariola\n")

    with pytest.raises(SynonymListError, match="kolica"):
        load_query_synonyms(path)


def test_a_word_listed_twice_with_and_without_accents_is_an_error(tmp_path: Path) -> None:
    # Search ignores accents, so both rules would fire for the same query (review R1-5).
    path = write(tmp_path, f"{SECTION}\n- tačke: kariola\n- tacke: kolica\n")

    with pytest.raises(SynonymListError, match="tacke"):
        load_query_synonyms(path)


def test_a_file_without_the_section_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, "## Nešto drugo\n- kolica: tačke\n")

    with pytest.raises(SynonymListError, match="Proširenja upita"):
        load_query_synonyms(path)


def test_a_section_without_rules_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, f"{SECTION}\n<!-- - kolica: tačke -->\n\n## Kasnije\n")

    with pytest.raises(SynonymListError, match="no rules"):
        load_query_synonyms(path)


def test_the_reviewed_list_ships_in_the_package_and_has_the_wheelbarrow_rules() -> None:
    # Package data next to this module, so a wheel or an image without docs/ has it.
    assert Path(synonyms_module.__file__).with_name("synonyms.md") == DEFAULT_PATH
    synonyms = dict(load_query_synonyms(DEFAULT_PATH).rules)

    assert {"tačke", "kariola", "civare"} <= set(synonyms["kolica"])
    # Tomislav (seed-14): "tačke" means wheelbarrows only, so it never expands to "kolica",
    # which would bring back the hand truck "KOLICA ZA TERET".
    assert "kolica" not in synonyms.get("tačke", ())


# --- default_query_synonyms: a bad list never breaks a search (review R1-2) -------------


@pytest.fixture
def fresh_cache() -> Iterator[None]:
    default_query_synonyms.cache_clear()
    yield
    default_query_synonyms.cache_clear()


@pytest.mark.usefixtures("fresh_cache")
def test_the_default_is_the_shipped_list() -> None:
    assert default_query_synonyms() == load_query_synonyms(DEFAULT_PATH)


@pytest.mark.usefixtures("fresh_cache")
def test_a_missing_list_means_no_expansion_and_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(synonyms_module, "DEFAULT_PATH", tmp_path / "missing.md")

    with caplog.at_level(logging.WARNING, logger="martom.search.synonyms"):
        assert default_query_synonyms() == QuerySynonyms()

    assert "synonym list not loaded" in caplog.text


@pytest.mark.usefixtures("fresh_cache")
def test_a_broken_list_means_no_expansion_and_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(synonyms_module, "DEFAULT_PATH", write(tmp_path, f"{SECTION}\nx\n"))

    with caplog.at_level(logging.WARNING, logger="martom.search.synonyms"):
        assert default_query_synonyms() == QuerySynonyms()

    [record] = caplog.records
    assert "line 7" in record.error  # `extra` fields become attributes of the log record
