"""The grounding check: every number in an answer has a source the model didn't invent.

CLAUDE.md: "Prices, product IDs and quantities in any answer come only from tools
(database, BOM calculator), never from the model." This is plain Python, no model.

Rather than listing the ways a price or a count can be written (and missing some: "KM
49,00", "49,- KM", "49 maraka", "12 stubova"), the check finds **every number** in the
answer and asks where it came from:

- A price (a number with a currency before or after it: KM, BAM, marka/maraka, €, EUR,
  or the ",-" of a whole amount) must equal a price a tool returned (`price`,
  `unit_price`, `line_total`, `total`). A euro amount always fails: Martom prices in KM.
- A listing id (7–9 digits) must be one a tool or the Retrieve step returned, and so
  must a manual product's key (a `manual_products.toml` key such as "anchor_m8x70",
  #132): a product without an OLX listing is identified by that key instead.
- A link (any http(s):// or www. address, and any address with a path even without
  one: "olx.ba/…", "martomshop.ba/…") must be a `url` a tool or the Retrieve step
  returned, exactly; a site named without a path ("OLX.ba") is not a link; markdown,
  quotes and brackets around it are not part of it. A manual product has no OLX
  listing (#132), so any link written for it fails, and so does an olx.ba/artikal/{id}
  link, which doesn't open.
- Bosnian number words ("pedeset tri KM", "dvanaest stubova") are read as numbers and
  checked the same way; a lone "jedan/jedna" (the article), numbered-list markers (but
  never a number with a currency after it) and a count of suggestions ("dva prijedloga",
  unless a product noun follows: "7 varijanti panela") are not claims. A rough count of
  products ("desetak stubova", "par panela") always fails: no tool gave it.
- Any other number must appear in a tool output (a quantity, a size in a title, a BOM
  line), in what the customer wrote, in the fence spec built from it, or in the advisor's
  fixed copy (agent/copy.py: Martom's phone, "10–15 dana", RAL colours).

A sentence that names a price together with something that never has one (a gate,
installation, "po narudžbi", "na upit", concrete posts, or an unpriced BOM line) fails
even when the amount itself is known.

The check is deliberately strict: a false alarm costs a retry (and at worst the plain
search results); a missed one would show the customer an invented price or quantity.
"""

# ruff: noqa: RUF001, RUF002, RUF003

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
# "Eurofence" is a product line (copy.EUROFENCE_*), not a euro amount (#134 R1-1).
_EURO = r"(?:€|EUR\b|eur[ao]?\b|euro(?!fenc)\w*)"
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
# A link: "https://…", "www.…", or a bare address with a path on any domain: "olx.ba/…",
# "martom.olx.ba/…" (R1-1), "martomshop.ba/…", "example.com/…" (#134, A1-2). A link stops
# at whitespace and at the characters a model wraps one in: markdown bold, code and
# [text](url), <autolinks>, quotes (also Bosnian „…“) and an ellipsis (A1-1). The bare
# form needs a path and a top-level domain of letters, so naming a site ("na OLX.ba",
# "martomshop.ba"), an email address or "24.90 KM/kom" is not a link.
_URL_CHAR = r"[^\s<>`*\"'“”„‘’\[\]()…]"
_URL = re.compile(
    rf"(?:https?://|www\.){_URL_CHAR}+"
    rf"|(?<![\w.@/-])(?:[\w-]+\.)+[a-z]{{2,24}}/{_URL_CHAR}+",
    re.IGNORECASE,
)
# Sentence punctuation after a link is not part of it.
_URL_TRAILING = ".,;:!?»"
# A manual product's key (manual_products.toml table name = BOM item key): "anchor_m8x70".
_MANUAL_KEY = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w-])")
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
# ... unless a currency follows it directly and no other number follows on that line: then
# it is a price ("Ukupno:\n12. KM", #134). "1. KM 53,00" (the price is 53,00) and
# "1. Eurofence panel" (a product line, not a euro) stay markers, so the euro form here is
# word-bounded instead of `_EURO`'s "euro\w*", and leaves out "euro" itself, which starts
# product titles ("1. EURO PANEL ZELENI"; #134 R1-1).
_MARKER_CURRENCY = rf"(?:{_KM}|€|EUR\b|eura\b|eur(?:ima|om)\b)"
_MARKER_PRICE = re.compile(
    rf"^\s*(?P<amount>\d{{1,2}})[.)]\s*(?P<currency>{_MARKER_CURRENCY})"
    # Only an amount right after the currency makes it a currency-first price ("1. KM
    # 53,00"); a number later on the line ("85. KM (2 panela)") doesn't (#134 R2-1).
    r"(?![^\S\n]*:?[^\S\n]*\d)",
    re.MULTILINE | re.IGNORECASE,
)
# "2 prijedloga", "dva prijedloga": how many suggestions the answer makes, not a quantity.
_SUGGESTION_NOUN = re.compile(
    r"\s+(?:prijedlog\w*|opcij\w*|varijant\w*|rješenj\w*|rjesenj\w*|mogućnost\w*|"
    r"mogucnost\w*|načina|nacina)\b",
    re.IGNORECASE,
)
MAX_SUGGESTIONS = 10
# Products and materials Martom sells, as Bosnian stems (#134, manager's decision): a count
# of suggestions followed by one of them ("7 varijanti panela", "pet opcija stubova") is
# checked like any count, and a rough count before one ("desetak stubova") fails. Before
# any other word both pass ("tri opcije iz naše ponude", "par savjeta", "par rukavica").
# One word may stand between them ("petnaestak dodatnih kopči"), unless it is a joining
# word ("dva prijedloga za stubove" is about suggestions). Add a product here when the
# advisor starts selling one.
_PRODUCT_NOUN = re.compile(
    r"(?:stub|stup|panel|kopč|kopc|žic|zic|anker|vijak|vijk|vijc|šaraf|saraf|matic|"
    r"podlošk|podlosk|zatezač|zatezac|zatezn|podupirač|podupirac|potporn|upornj|"
    r"rol(?:a|e|i|u|n|ama)|kapij|vrata\b|vratnic|mrež|mrez|ograd|pločic|plocic|"
    r"šipk|sipk|cijev|profil|traka|trake|traku|sajl|lanac|lanc|beton|cement|vreć|vrec|"
    r"kutij|paket|komad|kom\b|metar|metr|metara|"
    # BOM lines ("Zatezač (španer)", "Nosač nosive žice", "kolut 100 m"; R2-2) and
    # catalogue hardware.
    r"španer|spaner|nosač|nosac|kolut|obujmic|šark|sark|spojnic|kapic|tipl|brav|pletiv)\w*",
    re.IGNORECASE,
)
_JOIN_WORDS = frozenset(
    [
        "za",
        "u",
        "iz",
        "na",
        "s",
        "sa",
        "od",
        "do",
        "po",
        "prema",
        "i",
        "ili",
        "ali",
        "a",
        "kako",
        "što",
        "sto",
        "su",
        "je",
    ]
)
# Rough counts in words ("desetak stubova", "dvadesetak panela", "stotinjak vijaka", "par
# stubova"): a quantity no tool gave, so it fails before a product noun (#134); before a
# currency, `_VAGUE_PRICE` reports it. "nekoliko" is not one: it is too common outside
# counts ("Još nekoliko podataka:").
_ROUGH_COUNT = re.compile(
    r"\b(?:(?:jedanaest|dvanaest|trinaest|četrnaest|cetrnaest|petnaest|šesnaest|sesnaest|"
    r"sedamnaest|osamnaest|devetnaest|deset|dvadeset|trideset|četrdeset|cetrdeset|pedeset|"
    r"šezdeset|sezdeset|sedamdeset|osamdeset|devedeset)ak|stotin(?:j?ak)|par)\b",
    re.IGNORECASE,
)

# Bosnian number words (R2-2), with and without diacritics. "sto" is not in the table: without
# diacritics it is also "što" ("sto se tice ..."), so it counts as 100 only when a currency
# or another number word follows it ("sto KM", "sto pedeset KM"; `_sto_is_number`, R3-2).
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
# The number phrases of agent/copy.py; `test_every_copy_text_passes` keeps them in step.
_COPY_PHRASES = re.compile(
    r"\b10\s*[\u2013-]\s*15\s+dan\w*|\bRAL\s*(?:7016|6005)\b|\bod\s+0\s*m\b", re.IGNORECASE
)
# A currency after a vague amount ("pedesetak KM", "stotinjak maraka", "pola KM", "par
# maraka", "nekoliko KM"): a price no tool gave (R3-2). A currency after any other word,
# as in "Sve cijene su u KM.", is not a price.
_VAGUE_PRICE = re.compile(rf"\b(?:\w+ak|pola|par|nekoliko|koliko)\s+{_CURRENCY}", re.IGNORECASE)


class Facts(BaseModel):
    """What tools returned in this conversation; kept in the graph state as plain JSON."""

    prices: list[str] = Field(default_factory=list)  # Decimal as text, e.g. "53.00"
    listing_ids: list[int] = Field(default_factory=list)
    manual_keys: list[str] = Field(default_factory=list)  # manual products (#132)
    urls: list[str] = Field(default_factory=list)  # links tools returned (R1-1)
    numbers: list[str] = Field(default_factory=list)  # every other number, as Decimal text
    unpriced_names: list[str] = Field(default_factory=list)

    def merge(self, other: "Facts") -> "Facts":
        return Facts(
            prices=_unique(self.prices + other.prices),
            listing_ids=_unique(self.listing_ids + other.listing_ids),
            manual_keys=_unique(self.manual_keys + other.manual_keys),
            urls=_unique(self.urls + other.urls),
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
    manual_keys: list[str] = []
    urls: list[str] = []
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
                if key == "manual_key" and isinstance(item, str):
                    manual_keys.append(item)
                if key == "url" and isinstance(item, str):
                    urls.append(item)
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
        manual_keys=_unique(manual_keys),
        urls=_unique(urls),
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
    # Links first, then taken out: the digits of a slug aren't counts or prices.
    known_urls = set(facts.urls)
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING)
        if url not in known_urls:
            problems.append(f"link {url} is not in any tool result")
    text = _URL.sub(" ", text)
    # The numbers of the fixed copy count only inside their own phrase ("10–15 dana",
    # "RAL 7016"), never as a free count: "Trebate 10 stubova" must fail (R3a-1).
    text = _COPY_PHRASES.sub(" ", text)
    text = _PER_ONE.sub(" ", text)
    known_prices = facts.price_set()
    for match in _MARKER_PRICE.finditer(text):
        phrase = f"{match.group('amount')} {match.group('currency')}"
        if not re.fullmatch(_KM, match.group("currency"), re.IGNORECASE):
            problems.append(f"price '{phrase}' is in euros; Martom prices are in KM")
        elif Decimal(match.group("amount")) not in known_prices:
            problems.append(f"price '{phrase}' is not in any tool result")
    text = _MARKER_PRICE.sub(" ", text)
    text = _LIST_MARKER.sub(r"\1", text)
    text = _strip_suggestion_counts(text)
    # A manual product's key is checked like a listing id, then taken out, so the digits
    # inside it ("m8x70") aren't read as numbers.
    known_keys = set(facts.manual_keys)
    for match in _MANUAL_KEY.finditer(text):
        if match.group(0) not in known_keys:
            problems.append(f"product key {match.group(0)} is not in any tool result")
    text = _MANUAL_KEY.sub(" ", text)
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

    allowed = {Decimal(number) for number in facts.numbers} | known_prices | set(known_numbers)
    for match in _NUMBER.finditer(rest):
        number = parse_km(match.group(0))
        if number is None or number not in allowed:
            problems.append(
                f"number '{match.group(0)}' is not in any tool result or customer message"
            )
    problems.extend(_check_number_words(text, known_prices, allowed))
    problems.extend(_check_rough_counts(text))
    for match in _VAGUE_PRICE.finditer(text):
        problems.append(f"price '{match.group(0)}' is not an amount from any tool result")
    problems.extend(_priced_never_priced(text, facts.unpriced_names))
    return GroundingResult(problems)


@dataclass(frozen=True)
class _WordNumber:
    start: int
    end: int
    value: int
    words: int


def _sto_is_number(
    text: str, tokens: list[re.Match[str]], index: int, token: re.Match[str]
) -> bool:
    """ "sto" is 100 before a currency or another number word, never in "sto se tiče"."""
    if token.group(0).lower() != "sto":
        return False
    rest = text[token.end() :]
    if re.match(rf"\s+{_CURRENCY}", rest, re.IGNORECASE):
        return True
    if re.match(r"\s+i\s", rest, re.IGNORECASE) and index + 2 < len(tokens):
        index += 1  # "sto i pedeset": the joiner the parser allows elsewhere (R3a-2)
        token = tokens[index]
    following = tokens[index + 1] if index + 1 < len(tokens) else None
    return (
        following is not None
        and not text[token.end() : following.start()].strip()
        and following.group(0).lower() in (_WORD_VALUES.keys() | _THOUSAND_WORDS)
    )


def word_numbers(text: str) -> list[_WordNumber]:
    """Runs of Bosnian number words with their value: "četrdeset i devet" -> 49."""
    tokens = list(_WORD.finditer(text))
    values = {i: 100 for i, token in enumerate(tokens) if _sto_is_number(text, tokens, i, token)}
    found: list[_WordNumber] = []
    index = 0
    while index < len(tokens):
        word = tokens[index].group(0).lower()
        if word not in _WORD_VALUES and word not in _THOUSAND_WORDS and index not in values:
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
            elif word in _WORD_VALUES or index in values:
                value = values.get(index) or _WORD_VALUES[word]
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


# A hyphenated word is one token ("T-stupova"); its last part is the noun (#134 R2-2).
_NEXT_WORD = re.compile(r"[^\S\n]+(\w+(?:-\w+)*)")


def _is_product_noun(word: str) -> bool:
    return _PRODUCT_NOUN.fullmatch(word.rsplit("-", 1)[-1]) is not None


def _product_noun_after(text: str, end: int) -> str | None:
    """The words up to a product noun right after `end` (one word may stand between)."""
    first = _NEXT_WORD.match(text, end)
    if first is None:
        return None
    if _is_product_noun(first.group(1)):
        return first.group(1)
    if first.group(1).lower() in _JOIN_WORDS:
        return None
    second = _NEXT_WORD.match(text, first.end())
    if second is not None and _is_product_noun(second.group(1)):
        return f"{first.group(1)} {second.group(1)}"
    return None


def _is_suggestion_count(text: str, end: int) -> bool:
    """A suggestion noun starts at `end` and no product noun follows it (#134)."""
    noun = _SUGGESTION_NOUN.match(text, end)
    return noun is not None and _product_noun_after(text, noun.end()) is None


def _strip_suggestion_counts(text: str) -> str:
    """Drop "2 prijedloga" / "dva prijedloga" (up to MAX_SUGGESTIONS): not a quantity."""

    def digit(match: re.Match[str]) -> str:
        exempt = int(match.group(0)) <= MAX_SUGGESTIONS and _is_suggestion_count(text, match.end())
        return " " if exempt else match.group(0)

    text = re.sub(r"(?<![\d.,])\d{1,2}(?!\d|[.,]\d)", digit, text)
    for number in reversed(word_numbers(text)):
        if number.value <= MAX_SUGGESTIONS and _is_suggestion_count(text, number.end):
            text = text[: number.start] + " " + text[number.end :]
    return text


def _check_rough_counts(text: str) -> list[str]:
    problems = []
    for match in _ROUGH_COUNT.finditer(text):
        product = _product_noun_after(text, match.end())
        if product is not None:
            problems.append(
                f"rough count '{match.group(0)} {product}' is not a quantity from any tool result"
            )
    return problems


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
