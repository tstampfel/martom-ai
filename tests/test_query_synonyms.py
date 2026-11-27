"""The reviewed synonym list (docs/domain/synonyms.md) that keyword search expands queries with."""

from pathlib import Path

import pytest

from martom.search.synonyms import (
    DEFAULT_PATH,
    QuerySynonyms,
    SynonymListError,
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


def test_from_words_and_to_words_are_parallel_lists_for_sql() -> None:
    synonyms = QuerySynonyms(rules=(("kolica", ("tačke", "kariola")), ("španer", ("zatezač",))))

    assert synonyms.from_words == ["kolica", "španer"]
    assert synonyms.to_words == ["tačke kariola", "zatezač"]


def test_a_comment_in_the_section_is_ignored(tmp_path: Path) -> None:
    path = write(tmp_path, f"{SECTION}\n<!-- - a: b -->\n- kolica: tačke\n")

    assert load_query_synonyms(path).from_words == ["kolica"]


@pytest.mark.parametrize(
    "line",
    [
        "- ručna kolica: tačke",  # a phrase on the left
        "- kolica: ručne tačke",  # a phrase on the right
        "- kolica:",  # no synonyms
        "- kolica tačke",  # no colon
    ],
)
def test_a_malformed_rule_is_an_error_naming_its_line(tmp_path: Path, line: str) -> None:
    path = write(tmp_path, f"{SECTION}\n{line}\n")

    with pytest.raises(SynonymListError, match="line 7"):
        load_query_synonyms(path)


def test_a_word_listed_twice_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, f"{SECTION}\n- kolica: tačke\n- Kolica: kariola\n")

    with pytest.raises(SynonymListError, match="kolica"):
        load_query_synonyms(path)


def test_a_file_without_the_section_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, "## Nešto drugo\n- kolica: tačke\n")

    with pytest.raises(SynonymListError, match="Proširenja upita"):
        load_query_synonyms(path)


def test_the_reviewed_list_loads_and_has_the_wheelbarrow_rules() -> None:
    synonyms = dict(load_query_synonyms(DEFAULT_PATH).rules)

    assert {"tačke", "kariola", "civare"} <= set(synonyms["kolica"])
    # Tomislav (seed-14): "tačke" means wheelbarrows only, so it never expands to "kolica",
    # which would bring back the hand truck "KOLICA ZA TERET".
    assert "kolica" not in synonyms.get("tačke", ())
    assert Path(__file__).resolve().parents[1] / "docs/domain/synonyms.md" == DEFAULT_PATH
