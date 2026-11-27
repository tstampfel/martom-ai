"""The reviewed synonym list that keyword search expands customer queries with (#121).

Customers and listings use different regional words for the same product: a customer asks
for "kolica", the listing says "KARIOLA TAČKE". The list lives in `docs/domain/synonyms.md`,
reviewed by Tomislav and Toni; the code only reads it (it is data, never hard-coded here).

Format: under the heading `## Proširenja upita`, one rule per line,

    - kolica: tačke, kariola, karijola, civare

meaning "a query with the word *kolica* also matches listings that say tačke, kariola, ...".
A rule works one way; a two-way pair is written as two lines. Every word is a single word,
because keyword search ORs words: a phrase would match on any one of its words.

How a query word is matched to a rule (stemming, accents) is done in SQL, through
`martom_search_normalize` and the `martom` config, by `search/keyword.py`; this module only
reads and checks the file.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# src/martom/search/synonyms.py -> the repository root is three folders up from search/.
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "docs" / "domain" / "synonyms.md"
SECTION_HEADING = "## Proširenja upita"

_RULE = re.compile(r"^-\s+(?P<word>[^:\s]+)\s*:\s*(?P<synonyms>.+)$")
_SINGLE_WORD = re.compile(r"^\S+$")
_COMMENT = re.compile(r"<!--.*?-->")


class SynonymListError(ValueError):
    """The synonym file is missing its section or has a malformed rule."""


@dataclass(frozen=True)
class QuerySynonyms:
    """Rules as (query word, synonyms it adds), in file order."""

    rules: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def from_words(self) -> list[str]:
        """The query words, for SQL (a text[] parameter, parallel to `to_words`)."""
        return [word for word, _ in self.rules]

    @property
    def to_words(self) -> list[str]:
        """Each rule's synonyms joined by spaces, parallel to `from_words`."""
        return [" ".join(synonyms) for _, synonyms in self.rules]


def load_query_synonyms(path: Path = DEFAULT_PATH) -> QuerySynonyms:
    """Read and check the rules under `## Proširenja upita`; raises SynonymListError."""
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == SECTION_HEADING)
    except StopIteration:
        raise SynonymListError(f"{path}: no '{SECTION_HEADING}' section") from None

    rules: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for index in range(start + 1, len(lines)):
        line = _COMMENT.sub("", lines[index]).strip()
        if line.startswith("#"):  # the next heading ends the section
            break
        if not line.startswith("-"):
            continue
        number = index + 1  # editors count lines from 1
        match = _RULE.match(line)
        if match is None:
            raise SynonymListError(f"{path} line {number}: expected '- word: synonym, ...'")
        word = match["word"]
        synonyms = tuple(s.strip() for s in match["synonyms"].split(",") if s.strip())
        if not synonyms or not all(_SINGLE_WORD.match(s) for s in synonyms):
            raise SynonymListError(
                f"{path} line {number}: synonyms must be single words separated by commas"
            )
        if word.lower() in seen:
            raise SynonymListError(f"{path} line {number}: '{word.lower()}' is listed twice")
        seen.add(word.lower())
        rules.append((word, synonyms))
    return QuerySynonyms(rules=tuple(rules))


@cache
def default_query_synonyms() -> QuerySynonyms:
    """The reviewed list, read once per process (like a module-level constant)."""
    return load_query_synonyms(DEFAULT_PATH)
