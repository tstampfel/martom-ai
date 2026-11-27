"""The reviewed synonym list that keyword search expands customer queries with (#121).

Customers and listings use different regional words for the same product: a customer asks
for "kolica", the listing says "KARIOLA TAČKE". The list is `synonyms.md` next to this file
(package data, like bom/manual_products.toml, so every install and image has it), reviewed
by Tomislav and Toni; the code only reads it (it is data, never hard-coded here).

Format: under the heading `## Proširenja upita`, one rule per line,

    - kolica: tačke, kariola, karijola, civare
    - stup, stupovi: kolac

meaning "a query with the word *kolica* also matches listings that say tačke, kariola, ...".
Several words on the left share the synonyms (a plural that stems differently, such as
"stupovi", needs its own word). A rule works one way; a two-way pair is written as two
lines. Every word is a single word of letters and digits, because keyword search ORs words:
a phrase (or "ručna-kolica") would match on any one of its words.

Parsing is strict: HTML comments (`<!-- ... -->`, over any number of lines) are removed,
then every non-blank line of the section must be a rule, or a heading that ends it.
Anything else raises SynonymListError naming the line, so a rule is never dropped silently.

How a query word is matched to a rule (stemming, accents) is done in SQL, through
`martom_search_normalize` and the `martom` config, by `search/keyword.py`; this module only
reads and checks the file.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).with_name("synonyms.md")
SECTION_HEADING = "## Proširenja upita"

_RULE = re.compile(r"^-\s+(?P<words>[^:]+?)\s*:\s*(?P<synonyms>.+)$")
_WORD = re.compile(r"^[^\W_]+$")  # letters and digits only: no spaces, "-", "/" or "_"
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class SynonymListError(ValueError):
    """The synonym file is missing its section or rules, or has a malformed line."""


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


def _blank_comments(text: str) -> str:
    """The text with each comment replaced by its newlines, so line numbers stay right."""
    return _COMMENT.sub(lambda match: "\n" * match.group().count("\n"), text)


def _fold(word: str) -> str:
    """Lower case without accents ("Tačke" -> "tacke"), as search compares words."""
    decomposed = unicodedata.normalize("NFKD", word.lower().replace("đ", "d"))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _split_words(text: str) -> list[str]:
    return [word.strip() for word in text.split(",")]


def load_query_synonyms(path: Path = DEFAULT_PATH) -> QuerySynonyms:
    """Read and check the rules under `## Proširenja upita`; raises SynonymListError
    (or OSError when the file can't be read)."""
    lines = _blank_comments(path.read_text(encoding="utf-8")).splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == SECTION_HEADING)
    except StopIteration:
        raise SynonymListError(f"{path}: no '{SECTION_HEADING}' section") from None

    rules: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for index in range(start + 1, len(lines)):
        line = lines[index].strip()
        if not line:
            continue
        if line.startswith("#"):  # the next heading ends the section
            break
        where = f"{path} line {index + 1}"  # editors count lines from 1
        match = _RULE.match(line)
        if match is None:
            raise SynonymListError(f"{where}: expected '- word: synonym, synonym'")
        words = _split_words(match["words"])
        synonyms = tuple(_split_words(match["synonyms"]))
        if not all(_WORD.match(word) for word in [*words, *synonyms]):
            raise SynonymListError(
                f"{where}: every word must be a single word, separated by commas"
            )
        for word in words:
            if _fold(word) in seen:
                raise SynonymListError(f"{where}: '{_fold(word)}' already has a rule")
            seen.add(_fold(word))
            rules.append((word, synonyms))
    if not rules:
        raise SynonymListError(f"{path}: no rules under '{SECTION_HEADING}'")
    return QuerySynonyms(rules=tuple(rules))


@cache
def default_query_synonyms() -> QuerySynonyms:
    """The reviewed list, read once per process (like a module-level constant).

    A missing or broken file never breaks a search: it logs a warning and searches go on
    without expansion. The API reads it at startup, so the warning shows up at deploy time,
    and the tests fail on a broken shipped list before it gets that far.
    """
    try:
        return load_query_synonyms(DEFAULT_PATH)
    except (OSError, SynonymListError) as exc:
        logger.warning(
            "synonym list not loaded, searching without query expansion",
            extra={"path": str(DEFAULT_PATH), "error": str(exc)},
        )
        return QuerySynonyms()
