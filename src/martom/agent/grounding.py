"""The grounding check: every number in an answer has a source the model didn't invent.

CLAUDE.md: "Prices, product IDs and quantities in any answer come only from tools
(database, BOM calculator), never from the model." This is plain Python, no model.

Rather than listing the ways a price or a count can be written (and missing some: "KM
49,00", "49,- KM", "49 maraka", "12 stubova"), the check finds **every number** in the
answer and asks where it came from:

- A price (a number with a currency before or after it: KM, BAM, marka/maraka, €, EUR,
  or the ",-" of a whole amount) must equal a price a tool returned (`price`,
  `unit_price`, `line_total`, `total`). A euro amount always fails: Martom prices in KM.
- A listing id (7–9 digits) must be one a tool or the Retrieve step returned.
- Bosnian number words ("pedeset tri KM", "dvanaest stubova") are read as numbers and
  checked the same way; a lone "jedan/jedna" (the article) and numbered-list markers are
  not claims.
- Any other number must appear in a tool output (a quantity, a size in a title, a BOM
  line), in what the customer wrote, in the fence spec built from it, or in the advisor's
  fixed copy (agent/copy.py: Martom's phone, "10–15 dana", RAL colours).

A sentence that names a price together with something that never has one (a gate,
installation, "po narudžbi", "na upit", concrete posts, or an unpriced BOM line) fails
even when the amount itself is known.

The check is deliberately strict: a false alarm costs a retry (and at worst the plain
search results); a missed one would show the customer an invented price or quantity.
"""

# ruff: noqa: RUF002, RUF003

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from martom.agent import copy

PRICE_KEYS = frozenset({"price", "unit_price", "line_total", "total"})

# One amount: "1.234,50" / "1 234,50" (grouped thousands), "53,00", "24.90", "53", "49,-".
_AMOUNT = r"(?:\d{1,3}(?:[.\u00a0 ]\d{3})+(?:,\d{1,2}|,-)?|\d+(?:[.,]\d{1,2})?(?:,-)?)"
_KM = r"(?:KM\b|BAM\b|konvertibiln\w*\s+mar\w*|mar(?:ka|aka|ke|ki)\b)"
_EURO = r"(?:€|EUR\b|eur[ao]?\b|euro\w*)"
_CURRENCY = rf"(?:{_KM}|{_EURO})"
_PRICE = re.compile(
    rf"(?<![\d.,])(?P<after>{_AMOUNT})\s*(?P<cur_after>{_CURRENCY})"
    rf"|(?P<cur_before>{_CURRENCY})\s*(?P<before>{_AMOUNT})(?![\d.,]*\d)",
    re.IGNORECASE,
)
# A bare amount with the ",-" of a whole price ("49,-") is a price even without "KM".
_DASH_PRICE = re.compile(r"(?<![\d.,])(\d+(?:[.\u00a0 ]\d{3})*),-")
# OLX listing ids have 7 to 9 digits.
_LISTING_ID = re.compile(r"(?<![\d.,])\d{7,9}(?!\d|[.,]\d)")
# Any other number: "25", "25,5", "2.70".
_NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?!\d)")
# "53,00 KM za 1 kom" / "po 1 kom": the per-unit 1 isn't a quantity claim.
_PER_ONE = re.compile(r"(?:\bza|\bpo|/)\s*1\s*(?:kom\w*|komad\w*)", re.IGNORECASE)
# Things that never carry a price in an answer (fence-rules.md).
NEVER_PRICED = (
    "na upit",
    "po upitu",
    "po narudžbi",
    "po narudzbi",
    "kapij",
    "montaž",
    "montaz",
    "betoniranj",
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
# A numbered list marker at the start of a line ("1. PANEL", "2) Stup"): not a claim (R2-3).
_LIST_MARKER = re.compile(r"^(\s*)\d{1,2}[.)](?=\s)", re.MULTILINE)
# "2 prijedloga", "dva prijedloga": how many suggestions the answer makes, not a quantity.
_SUGGESTION_NOUN = re.compile(
    r"\s+(?:prijedlog\w*|opcij\w*|varijant\w*|rješenj\w*|rjesenj\w*|mogućnost\w*|"
    r"mogucnost\w*|načina|nacina)\b",
    re.IGNORECASE,
)
MAX_SUGGESTIONS = 10

# Bosnian number words (R2-2), with and without diacritics. "sto" alone is left out: without
# diacritics it is also "što" ("sto se tice ..."); "stotinu" and "dvjesto" ... are kept.
_WORD_VALUES: dict[str, int] = {
    **dict.fromkeys(["jedan", "jedna", "jedno", "jednu", "jednog", "jednom", "jednoj"], 1),
    **dict.fromkeys(["dva", "dvije", "dvoje", "dvaju"], 2),
    **dict.fromkeys(["tri", "troje"], 3),
    **dict.fromkeys(["četiri", "cetiri"], 4),
    "pet": 5,
    **dict.fromkeys(["šest", "sest"], 6),
    "sedam": 7,
    "osam": 8,
    "devet": 9,
    "deset": 10,
    "jedanaest": 11,
    "dvanaest": 12,
    "trinaest": 13,
    **dict.fromkeys(["četrnaest", "cetrnaest"], 14),
    "petnaest": 15,
    **dict.fromkeys(["šesnaest", "sesnaest"], 16),
    "sedamnaest": 17,
    "osamnaest": 18,
    "devetnaest": 19,
    "dvadeset": 20,
    "trideset": 30,
    **dict.fromkeys(["četrdeset", "cetrdeset"], 40),
    "pedeset": 50,
    **dict.fromkeys(["šezdeset", "sezdeset"], 60),
    "sedamdeset": 70,
    "osamdeset": 80,
    "devedeset": 90,
    "stotinu": 100,
    **dict.fromkeys(["dvjesto", "dvjesta", "dvesta"], 200),
    "tristo": 300,
    **dict.fromkeys(["četiristo", "cetiristo"], 400),
    "petsto": 500,
    **dict.fromkeys(["šeststo", "sesto", "šesto"], 600),
    "sedamsto": 700,
    "osamsto": 800,
    "devetsto": 900,
}
_THOUSAND_WORDS = frozenset(
    ["hiljadu", "hiljade", "hiljada", "tisuću", "tisucu", "tisuće", "tisuce", "tisuća", "tisuca"]
)
_WORD = re.compile(r"\w+")


class Facts(BaseModel):
    """What tools returned in this conversation; kept in the graph state as plain JSON."""

    prices: list[str] = Field(default_factory=list)  # Decimal as text, e.g. "53.00"
    listing_ids: list[int] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)  # every other number, as Decimal text
    unpriced_names: list[str] = Field(default_factory=list)

    def merge(self, other: "Facts") -> "Facts":
        return Facts(
            prices=_unique(self.prices + other.prices),
            listing_ids=_unique(self.listing_ids + other.listing_ids),
            numbers=_unique(self.numbers + other.numbers),
            unpriced_names=_unique(self.unpriced_names + other.unpriced_names),
        )

    def price_set(self) -> set[Decimal]:
        # Decimal("53.00") == Decimal("53"), so "53 KM" matches a "53.00" fact.
        return {Decimal(price) for price in self.prices}


@dataclass(frozen=True)
class GroundingResult:
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def facts_from_output(output: Any, *, skip_keys: Iterable[str] = ()) -> Facts:
    """Every price, listing id, number and unpriced line name in a tool's JSON output.

    `skip_keys`: keys whose values only echo the model's own input (the cart's quantity),
    so they never become a fact.
    """
    skipped = frozenset(skip_keys)
    prices: list[str] = []
    listing_ids: list[int] = []
    numbers: list[str] = []
    unpriced: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in skipped:
                    continue
                if key in PRICE_KEYS and item is not None:
                    price = _decimal(item)
                    if price is not None:
                        prices.append(str(price))
                        continue
                if key == "listing_id" and _is_int(item):
                    listing_ids.append(item)
                walk(item)
            if value.get("price_on_request") is True and isinstance(value.get("name"), str):
                unpriced.append(value["name"])
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            numbers.extend(str(number) for number in numbers_in(value))
        elif _is_int(value) or isinstance(value, float | Decimal):
            number = _decimal(value)
            if number is not None:
                numbers.append(str(number))

    walk(output)
    return Facts(
        prices=_unique(prices),
        listing_ids=_unique(listing_ids),
        numbers=_unique(numbers),
        unpriced_names=_unique(unpriced),
    )


def numbers_in(text: str) -> list[Decimal]:
    """Every number written in `text` (customer text, titles), read like the answer is."""
    found = [
        parse_km(match.group("after") or match.group("before")) for match in _PRICE.finditer(text)
    ]
    rest = _PRICE.sub(" ", text)
    found += [parse_km(match.group(0)) for match in _NUMBER.finditer(rest)]
    found += [Decimal(number.value) for number in word_numbers(text)]
    return [number for number in found if number is not None]


def parse_km(text: str) -> Decimal | None:
    """'1.234,50' / '1 234,50' -> 1234.50, '53,00' -> 53.00, '49,-' -> 49; None if not one."""
    text = text.strip().removesuffix(",-").replace("\u00a0", " ")
    if re.fullmatch(r"\d{1,3}(?:[. ]\d{3})+(?:,\d+)?", text):
        text = text.replace(".", "").replace(" ", "")  # thousands groups
    text = text.replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def check_grounding(
    text: str, facts: Facts, *, known_numbers: Iterable[Decimal] = ()
) -> GroundingResult:
    """Each problem is one short English line, for the retry prompt and the logs.

    `known_numbers`: numbers from the customer (their messages and the fence spec), which
    the answer may repeat, but never as a price.
    """
    problems: list[str] = []
    text = text.replace(copy.MARTOM_PHONE, " ")
    text = _PER_ONE.sub(" ", text)
    text = _LIST_MARKER.sub(r"\1", text)
    text = _strip_suggestion_counts(text)
    known_prices = facts.price_set()
    for match in _PRICE.finditer(text):
        amount = parse_km(match.group("after") or match.group("before"))
        currency = match.group("cur_after") or match.group("cur_before")
        if re.fullmatch(_EURO, currency, re.IGNORECASE):
            problems.append(f"price '{match.group(0)}' is in euros; Martom prices are in KM")
        elif amount is None or amount not in known_prices:
            problems.append(f"price '{match.group(0)}' is not in any tool result")
    rest = _PRICE.sub(" ", text)
    for match in _DASH_PRICE.finditer(rest):
        if parse_km(match.group(1)) not in known_prices:
            problems.append(f"price '{match.group(0)}' is not in any tool result")
    rest = _DASH_PRICE.sub(" ", rest)

    known_ids = set(facts.listing_ids)
    for match in _LISTING_ID.finditer(rest):
        if int(match.group(0)) not in known_ids:
            problems.append(f"listing id {match.group(0)} is not in any tool result")
    rest = _LISTING_ID.sub(" ", rest)

    allowed = (
        {Decimal(number) for number in facts.numbers}
        | known_prices
        | set(known_numbers)
        | _COPY_NUMBERS
    )
    for match in _NUMBER.finditer(rest):
        number = parse_km(match.group(0))
        if number is None or number not in allowed:
            problems.append(
                f"number '{match.group(0)}' is not in any tool result or customer message"
            )
    problems.extend(_check_number_words(text, known_prices, allowed))
    problems.extend(_priced_never_priced(text, facts.unpriced_names))
    return GroundingResult(problems)


@dataclass(frozen=True)
class _WordNumber:
    start: int
    end: int
    value: int
    words: int


def word_numbers(text: str) -> list[_WordNumber]:
    """Runs of Bosnian number words with their value: "četrdeset i devet" -> 49."""
    tokens = list(_WORD.finditer(text))
    found: list[_WordNumber] = []
    index = 0
    while index < len(tokens):
        word = tokens[index].group(0).lower()
        if word not in _WORD_VALUES and word not in _THOUSAND_WORDS:
            index += 1
            continue
        start, total, current, count = tokens[index].start(), 0, 0, 0
        end, last = start, None
        while index < len(tokens):
            token = tokens[index]
            # One number only while words follow with spaces ("četrdeset i devet") and each
            # part is smaller than the one before ("dva tri" is 2 and 3).
            if count and text[end : token.start()].strip():
                break
            word = token.group(0).lower()
            if word in _THOUSAND_WORDS:
                if total:
                    break
                total, current, last = (current or 1) * 1000, 0, 1000
            elif word in _WORD_VALUES:
                value = _WORD_VALUES[word]
                if last is not None and value >= last:
                    break
                current, last = current + value, value
            elif (
                word == "i"
                and count
                and index + 1 < len(tokens)
                and tokens[index + 1].group(0).lower() in _WORD_VALUES
            ):
                end = token.end()  # "četrdeset i devet": the "i" joins, it isn't a gap
                index += 1
                continue
            else:
                break
            count += 1
            end = token.end()
            index += 1
        found.append(_WordNumber(start, end, total + current, count))
    return found


def _check_number_words(text: str, known_prices: set[Decimal], allowed: set[Decimal]) -> list[str]:
    problems = []
    for number in word_numbers(text):
        phrase = text[number.start : number.end]
        after = re.match(rf"\s*({_CURRENCY})", text[number.end :], re.IGNORECASE)
        before = re.search(rf"({_CURRENCY})\s*$", text[: number.start], re.IGNORECASE)
        currency = (after or before).group(1) if (after or before) else None
        if currency is not None:
            if re.fullmatch(_EURO, currency, re.IGNORECASE):
                problems.append(f"price '{phrase} {currency}' is in euros; Martom prices are in KM")
            elif Decimal(number.value) not in known_prices:
                problems.append(f"price '{phrase} {currency}' is not in any tool result")
        elif number.value == 1 and number.words == 1:
            continue  # "jedan", "jednu": the article "a/one", like the per-unit 1
        elif Decimal(number.value) not in allowed:
            problems.append(f"number '{phrase}' is not in any tool result or customer message")
    return problems


def _strip_suggestion_counts(text: str) -> str:
    """Drop "2 prijedloga" / "dva prijedloga" (up to MAX_SUGGESTIONS): not a quantity."""

    def digit(match: re.Match[str]) -> str:
        return " " if int(match.group(1)) <= MAX_SUGGESTIONS else match.group(0)

    text = re.sub(
        r"(?<![\d.,])(\d{1,2})(?=" + _SUGGESTION_NOUN.pattern + ")",
        digit,
        text,
        flags=re.IGNORECASE,
    )
    for number in reversed(word_numbers(text)):
        if number.value <= MAX_SUGGESTIONS and _SUGGESTION_NOUN.match(text, number.end):
            text = text[: number.start] + " " + text[number.end :]
    return text


def _priced_never_priced(text: str, unpriced_names: Iterable[str]) -> list[str]:
    words = [*NEVER_PRICED, *(name.lower() for name in unpriced_names)]
    problems = []
    for sentence in _SENTENCE_END.split(text):
        lowered = sentence.lower()
        priced = _PRICE.search(sentence) or _DASH_PRICE.search(sentence)
        if priced and any(word in lowered for word in words):
            problems.append(
                f"sentence '{sentence.strip()}' gives a price for something that is "
                "never priced (gate, installation, made to order or on request)"
            )
    return problems


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unique[T](items: list[T]) -> list[T]:
    # dict keeps insertion order (like a JS Map), so this dedupes without reordering.
    return list(dict.fromkeys(items))


# Numbers in the advisor's own fixed texts (phone groups, "10–15 dana", RAL 7016 ...).
_COPY_NUMBERS: frozenset[Decimal] = frozenset(
    number
    for value in vars(copy).values()
    if isinstance(value, str)
    for number in numbers_in(value)
)
