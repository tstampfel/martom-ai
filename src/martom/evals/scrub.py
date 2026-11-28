"""Strip personal data from a real chat before it is promoted to an eval set (#129).

Every path that copies a customer chat into something kept longer than the chats
themselves (a LangSmith dataset or feedback example, `evals/*.jsonl`) must pass it
through `scrub_personal_data` first. It replaces:

- phone numbers (BiH and HR, national and +387/+385/00 forms) with "[telefon]", except
  Martom's public shop number 036 651 427;
- e-mail addresses with "[email]", except Martom's own (martom-bekaert.com);
- names with "[ime]": the names the customer typed into the order form (pass them as
  `names`, matched ignoring accents, case and Bosnian endings) and names the customer
  introduces ("Zovem se ...", "Ime: ...");
- addresses with "[adresa]": the order form's address (`addresses`), labelled ones
  ("Adresa: ..."), streets with a house number and postal code + town.

Patterns can't catch every name or address a customer types, so pass the order form's
values whenever there is one, and still read a promoted chat before committing it
(`tests/test_fixture_privacy.py` keeps guarding the committed files). The function only
ever removes text; it errs on the side of removing too much.
"""

import re
from collections.abc import Iterable
from typing import overload

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

PHONE = "[telefon]"
EMAIL = "[email]"
NAME = "[ime]"
ADDRESS = "[adresa]"

# Martom's public numbers and e-mail domain may stay (CLAUDE.md, public repo rule).
SHOP_PHONE_DIGITS = frozenset({"036651427", "38736651427"})
SHOP_EMAIL_DOMAIN = "martom-bekaert.com"

_UPPER = "A-ZČĆŽŠĐ"
_LOWER = "a-zčćžšđ"
_CAPITALISED = f"[{_UPPER}][{_LOWER}]+"

# One separator between digit groups: a space, "/" or "-", optionally with spaces around
# it ("061 / 234-567"). No dots, so a date like 01.12.2026 is never a phone.
_SEP = r"(?:\s?[/-]\s?|\s)?"
_PHONE_RE = re.compile(
    r"(?<![\w+])"
    r"(?:"
    rf"(?:\+|00)38[57]\s*(?:\(0\)\s*)?\d(?:{_SEP}\d){{6,9}}"  # +387 61 234 567, +385 1 ...
    rf"|0\d(?:{_SEP}\d){{6,9}}"  # 061 234 567, 01 2345 678
    r")"
    r"(?!\w)"
)

_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")

# "Ime: Ana Babić", "Ime i prezime: ..." up to the end of the line or a comma;
# "Adresa: ..." up to the end of the line (the town goes with it).
_NAME_LABEL_RE = re.compile(r"(?im)\b((?:ime i prezime|ime|prezime)\s*:[ \t]*)[^\n,;]+")
_ADDRESS_LABEL_RE = re.compile(r"(?im)\b(adresa(?: za dostavu)?\s*:[ \t]*)[^\n]+")

# "Zovem se Marko Kovač": only capitalised words count, so "ja sam iz Čitluka" stays.
_INTRO_RE = re.compile(
    rf"\b((?i:zovem se|moje ime je|ime mi je|ja sam)\s+){_CAPITALISED}(?:\s+{_CAPITALISED}){{0,2}}"
)

# A number followed by one of these is a size, a price or a count, never a house number.
_NOT_HOUSE_NUMBER = (
    r"(?!\s*(?i:cm|mm|m|km|kg|kom\w*|komad\w*|metr\w*|metar|stub\w*|panel\w*|x|\u00d7|%)\b"
    r"|[,.]\d|\s*[x\u00d7*])"
)
_HOUSE_NUMBER = rf"(?:\d{{1,4}}[a-zA-Z]?\b{_NOT_HOUSE_NUMBER}|bb\b)"
# "ulica Stjepana Radića 5a", "Ul. Bana Jelačića bb", "Trg slobode 3". No "put": it also
# means "time" ("prvi put kupujem 3 panela").
_STREET_KEYWORD_RE = re.compile(
    rf"(?i)\b(?:ulica|ulici|ulicu|ul\.|cesta|cesti|trg|bulevar|naselje)\s+"
    rf"(?:[^\W\d_]+\.?\s+){{1,4}}{_HOUSE_NUMBER}"
)
# Two or three capitalised words and a house number: "Kralja Tomislava 12".
_STREET_RE = re.compile(rf"\b(?:{_CAPITALISED}\s+){{2,3}}{_HOUSE_NUMBER}")
# Postal code and town: "88260 Čitluk".
_POSTAL_RE = re.compile(rf"\b\d{{5}}\s+{_CAPITALISED}")

# Letters a customer may type without their accent.
_ACCENT_CLASSES = {"c": "[cčć]", "s": "[sš]", "z": "[zž]", "d": "[dđ]"}
_UNACCENT = str.maketrans("čćšžđČĆŠŽĐ", "ccszdCCSZD")


@overload
def scrub_personal_data(
    value: str, *, names: Iterable[str] = (), addresses: Iterable[str] = ()
) -> str: ...
@overload
def scrub_personal_data(
    value: JsonValue, *, names: Iterable[str] = (), addresses: Iterable[str] = ()
) -> JsonValue: ...
def scrub_personal_data(
    value: JsonValue, *, names: Iterable[str] = (), addresses: Iterable[str] = ()
) -> JsonValue:
    """Return a copy of `value` with names, phones, e-mails and addresses replaced.

    `value` is a string or a JSON-like record (dicts and lists are walked, every string
    value is scrubbed, keys and numbers are kept). `names` and `addresses` are the values
    from the order form, when the chat has one.
    """
    name_res = [_known_name_re(token) for name in names for token in name.split() if len(token) > 1]
    address_res = [_known_address_re(address) for address in addresses if address.strip()]
    return _scrub(value, name_res, address_res)


def _scrub(
    value: JsonValue, names: list[re.Pattern[str]], addresses: list[re.Pattern[str]]
) -> JsonValue:
    # `match` on the type, like a TS switch over typeof.
    match value:
        case str():
            return _scrub_text(value, names, addresses)
        case list():
            return [_scrub(item, names, addresses) for item in value]
        case dict():
            return {key: _scrub(item, names, addresses) for key, item in value.items()}
        case _:
            return value


def _scrub_text(text: str, names: list[re.Pattern[str]], addresses: list[re.Pattern[str]]) -> str:
    text = _EMAIL_RE.sub(_email_replacement, text)
    text = _PHONE_RE.sub(_phone_replacement, text)
    text = _ADDRESS_LABEL_RE.sub(rf"\g<1>{ADDRESS}", text)
    text = _NAME_LABEL_RE.sub(rf"\g<1>{NAME}", text)
    for address in addresses:
        text = address.sub(ADDRESS, text)
    text = _STREET_KEYWORD_RE.sub(ADDRESS, text)
    text = _STREET_RE.sub(ADDRESS, text)
    text = _POSTAL_RE.sub(ADDRESS, text)
    text = _INTRO_RE.sub(rf"\g<1>{NAME}", text)
    for name in names:
        text = name.sub(NAME, text)
    return text


def _phone_replacement(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    digits = digits.removeprefix("00")
    return match.group() if digits in SHOP_PHONE_DIGITS else PHONE


def _email_replacement(match: re.Match[str]) -> str:
    domain = match.group(1).lower()
    keep = domain == SHOP_EMAIL_DOMAIN or domain.endswith("." + SHOP_EMAIL_DOMAIN)
    return match.group() if keep else EMAIL


def _accent_insensitive(text: str) -> str:
    plain = text.translate(_UNACCENT).lower()
    return "".join(_ACCENT_CLASSES.get(char, re.escape(char)) for char in plain)


def _known_name_re(token: str) -> re.Pattern[str]:
    # Up to four more letters cover Bosnian endings: Ivan -> Ivanu, Šimić -> Šimićeva.
    return re.compile(rf"(?i)\b{_accent_insensitive(token)}\w{{0,4}}\b")


def _known_address_re(address: str) -> re.Pattern[str]:
    parts = (_accent_insensitive(part) for part in address.split())
    return re.compile(r"(?i)" + r"[\s,]+".join(parts))
