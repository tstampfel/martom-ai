"""Strip personal data from a real chat before it is promoted to an eval set (#129).

Every path that copies a customer chat into something kept longer than the chats
themselves (a LangSmith dataset or feedback example, `evals/*.jsonl`) must pass it
through `scrub_personal_data` first. It replaces:

- phone numbers with "[telefon]": BiH and HR numbers with a leading 0, "(061)", "+387",
  "00387" or "387", grouped with spaces, "/", "-" or "."; and a number without its 0
  right after a phone word ("moj broj je ...", "broj telefona", "tel:", "mob."), or after
  a bare "broj" / "kontakt" only when its digits are grouped like a phone ("61 000 000").
  An OLX listing id (7-9 digits in one run: "artikal broj 61234567") always stays.
  Martom's public shop number 036 651 427 stays. A date (01/12/2026, 03 12 2026) is never
  a phone;
- e-mail addresses with "[email]", except Martom's own (martom-bekaert.com);
- names with "[ime]": the names the customer typed into the order form (pass them as
  `names`; matched ignoring case, accents, "dj" for "đ" and Bosnian endings) and names
  the customer introduces ("Zovem se ...", "ime mi je ...", in any case; "Ja sam ..."
  only with a capital letter, since "ja sam iz Čitluka" is not a name; "Ime: ...");
- addresses with "[adresa]": the order form's address (`addresses`), labelled ones
  ("Adresa: ..." up to the end of the sentence), a street word with a house number
  ("ulica ... 5a", "Ul. ... bb", "Trg ... 3"), a street after a delivery or living verb
  ("dostava na ... 12", "stanujem u ... 4b"), a street ending in "bb", and a postal
  code with its town ("88000 Mostar").

Addresses are recognised only by such a label or context, never by capitalised words
and a number alone: product text ("Panel Nylofor 3D 153 cm", "Kolica Tačke 85 L") must
reach the eval set unchanged, or the eval measures something else.

Known misses, so the human reading a promoted chat knows what to look for: a name the
customer gives without an intro and without the order form ("Pozdrav, Pero"), a street
with no label, street word or delivery verb, even with a house number ("Izmišljena 12,
Čitluk"), a phone without its 0 written in one run after a bare "broj" (it looks like a
listing id), foreign phone numbers, and numbers written as words. Pass the order form's
values whenever there is one, and still read a promoted chat before committing it
(`tests/test_fixture_privacy.py` keeps guarding the committed files). The function only
ever removes text.
"""

import re
from collections.abc import Iterable
from typing import overload

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

PHONE = "[telefon]"
EMAIL = "[email]"
NAME = "[ime]"
ADDRESS = "[adresa]"

# Martom's public numbers and e-mail domain may stay (CLAUDE.md, public repo rule): the
# shop number's digits in each way it can be written, with any 00 prefix removed.
SHOP_PHONE_DIGITS = frozenset({"036651427", "36651427", "38736651427", "387036651427"})
SHOP_EMAIL_DOMAIN = "martom-bekaert.com"

_UPPER = "A-ZČĆŽŠĐ"
_LOWER = "a-zčćžšđ"
_CAPITALISED = f"[{_UPPER}][{_LOWER}]+"
_WORD = r"[^\W\d_]+"  # letters only, any case, any accent

# --- phones -------------------------------------------------------------------------

# One separator between digits: a space, "/", "-" or ".", optionally with spaces around
# the punctuation ("061 / 000 000").
_SEP = r"(?:\s?[/.-]\s?|\s)?"
_PHONE_RE = re.compile(
    r"(?<![\w+])"
    r"(?:"
    rf"(?:\+|00)?38[57]\s*(?:\(0\)\s*)?\d(?:{_SEP}\d){{6,9}}"  # +387 61 000 000, 387 61 ...
    rf"|\(0\d{{1,2}}\)\s?\d(?:{_SEP}\d){{5,7}}"  # (061) 000-000
    rf"|0\d(?:{_SEP}\d){{6,9}}"  # 061 000 000, 01 0000 000
    r")"
    r"(?!\w)"
)
# A number without its leading 0, right after a word that can only mean a phone:
# "moj broj je 61 000 000", "broj telefona 61000000", "tel: 61/000-000".
_PHONE_AFTER_PHONE_WORD_RE = re.compile(
    r"(?i)(\b(?:(?:moj\s+broj|broj\s+(?:telefona|mobitela|mobilnog))(?:\s+je)?"
    r"|tel\.?|telefon|telefona|mob\.?|mobitel|mobitela|mobilni)\s*:?\s*)"
    rf"([1-9]\d(?:{_SEP}\d){{5,8}})(?!\w)"
)
# After a bare "broj" or "kontakt" the number may be a listing id (7-9 digits, R2-1), so
# only digits grouped the way phones are written count ("broj je 61 000 000"), and never
# after a listing word ("artikal broj ...", "oglas broj ...", "šifra ...").
_PHONE_AFTER_BROJ_RE = re.compile(
    r"(?i)(?:\b(artikl\w*|oglas\w*|[sš]ifr\w*)\s+)?"
    r"(\b(?:broj|kontakt)(?:\s+je)?\s*:?\s*)"
    r"([1-9]\d{1,2}(?:(?:\s?[/.-]\s?|\s)\d{2,4}){2,3})(?!\w)"
)
# Day, month and year: never a phone, however it is separated.
_DATE_RE = re.compile(
    r"\d{1,2}\s?([./-])\s?\d{1,2}\s?\1\s?\d{2,4}"  # 01/12/2026, 01.12.2026
    r"|\d{1,2}\s\d{1,2}\s\d{4}"  # 03 12 2026
)

_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")

# --- names --------------------------------------------------------------------------

# "Ime: Ana Anić", "Ime i prezime: ..." up to the end of the line or a comma.
# An initial's dot ("Ana M. Anić") doesn't end it.
_NAME_LABEL_RE = re.compile(
    rf"(?im)\b((?:ime i prezime|ime|prezime)\s*:[ \t]*)(?:\b[{_UPPER}]\.|[^\n,;.<])+"
)
# Words that follow an intro but are not a name: "zovem se Pero i trebam ...".
_NOT_A_NAME = r"(?!(?i:i|a|ali|te|pa|iz|sa|s|trebam|treba|hoću|hocu|želim|zelim|imam)\b)"
# "zovem se pero perić": any case, as customers type. One to three words ("Ana Marija
# Anić").
_INTRO_RE = re.compile(
    rf"\b((?i:zovem se|moje ime je|ime mi je)\s+){_NOT_A_NAME}{_WORD}"
    rf"(?:\s+{_NOT_A_NAME}{_WORD}){{0,2}}"
)
# "Ja sam Ana Anić": capitalised words only, so "ja sam iz Čitluka" stays.
_JA_SAM_RE = re.compile(rf"\b((?i:ja sam)\s+){_CAPITALISED}(?:\s+{_CAPITALISED}){{0,2}}")

# --- addresses ----------------------------------------------------------------------

# "Adresa: ..." up to the end of the line, a sentence or a tag; "Ul." and "br." don't end
# it, and the town after a comma goes with it.
_ADDRESS_LABEL_RE = re.compile(
    r"(?im)\b(adresa(?: za dostavu)?\s*:[ \t]*)(?:\b(?:ul|br)\.|[^\n.!?<])+"
)
# A number followed by one of these is a size, a price or a count, never a house number.
_NOT_HOUSE_NUMBER = (
    r"(?!\s*(?i:cm|mm|m|km|kg|l|kom\w*|komad\w*|metr\w*|metar|stub\w*|panel\w*|palet\w*"
    r"|dan\w*|sat\w*|sedmic\w*|tjed\w*|min\w*|h|x|\u00d7|%)\b"
    # "15. decembra" (a date), "54,90" (a price), "153x250" (a size).
    r"|\.\s*[a-zčćžšđ]|[,.]\d|\s*[x\u00d7*/])"
)
_HOUSE_NUMBER = rf"(?:\d{{1,4}}[a-zA-Z]?\b{_NOT_HOUSE_NUMBER}|bb\b)"
_STREET_WORDS = rf"(?:{_WORD}\.?\s+)"
# "ulici Izmišljenoj 5a", "Ul. Izmišljena bb", "Trg izmišljenih 3". No "put": it also
# means "time" ("prvi put kupujem 3 panela").
_STREET_KEYWORD_RE = re.compile(
    r"(?i)\b(?:ulica|ulici|ulicu|ul\.|cesta|cesti|trg|bulevar|naselje)\s+"
    rf"{_STREET_WORDS}{{0,4}}{_HOUSE_NUMBER}"
)
# "dostava na kralja izmišljenog 12", "stanujem u Izmišljenoj 4b": the verb stays.
_STREET_AFTER_VERB_RE = re.compile(
    r"(?i)\b((?:dostav\w*|po[sš]alj\w*|[sš]alj\w*|isporu\w*|živim|zivim|stanujem)"
    r"\s+(?:na|u)\s+)"
    rf"{_STREET_WORDS}{{1,4}}{_HOUSE_NUMBER}"
)
# "Izmišljena bb" (bez broja): only streets end in "bb".
_BB_STREET_RE = re.compile(
    r"(?i)\b(?!(?:je|na|u|i|za|do|od|sa|s|a)\s)" rf"{_STREET_WORDS}{{1,2}}bb\b"
)
# Postal code and town: "88000 Mostar", after a comma or at the start of a line (as in
# "..., 88000 Mostar"), so "Kod 12345 Panel" stays.
_POSTAL_RE = re.compile(rf"(?m)(?:(?<=,)|(?<=, )|^)\d{{5}}\s+{_CAPITALISED}")

# --- known names and addresses from the order form ----------------------------------

# Letters a customer may type without their accent; "đ" is also written "dj" or "d".
_UNACCENT = str.maketrans("čćšž", "ccsz")
_LETTER_CLASSES = {"c": "[cčć]", "s": "[sš]", "z": "[zž]"}
_DJ = "(?:dj|[dđ])"
_VOWELS = "aeiou"


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
    text = _PHONE_AFTER_PHONE_WORD_RE.sub(_phone_after_word_replacement, text)
    text = _PHONE_AFTER_BROJ_RE.sub(_phone_after_broj_replacement, text)
    text = _ADDRESS_LABEL_RE.sub(rf"\g<1>{ADDRESS}", text)
    text = _NAME_LABEL_RE.sub(rf"\g<1>{NAME}", text)
    for address in addresses:
        text = address.sub(ADDRESS, text)
    text = _STREET_KEYWORD_RE.sub(ADDRESS, text)
    text = _STREET_AFTER_VERB_RE.sub(rf"\g<1>{ADDRESS}", text)
    text = _BB_STREET_RE.sub(ADDRESS, text)
    text = _POSTAL_RE.sub(ADDRESS, text)
    text = _INTRO_RE.sub(rf"\g<1>{NAME}", text)
    text = _JA_SAM_RE.sub(rf"\g<1>{NAME}", text)
    for name in names:
        text = name.sub(NAME, text)
    return text


def _is_shop_phone(number: str) -> bool:
    return re.sub(r"\D", "", number).removeprefix("00") in SHOP_PHONE_DIGITS


def _phone_replacement(match: re.Match[str]) -> str:
    number = match.group()
    if _is_shop_phone(number) or _DATE_RE.fullmatch(number):
        return number
    return PHONE


def _phone_after_word_replacement(match: re.Match[str]) -> str:
    prefix, number = match.group(1), match.group(2)
    if _is_shop_phone(number) or _DATE_RE.fullmatch(number):
        return match.group()
    return prefix + PHONE


def _phone_after_broj_replacement(match: re.Match[str]) -> str:
    listing_word, prefix, number = match.group(1), match.group(2), match.group(3)
    if listing_word or _is_shop_phone(number) or _DATE_RE.fullmatch(number):
        return match.group()
    # Without a listing word the match is just the prefix and the number.
    return prefix + PHONE


def _email_replacement(match: re.Match[str]) -> str:
    domain = match.group(1).lower()
    keep = domain == SHOP_EMAIL_DOMAIN or domain.endswith("." + SHOP_EMAIL_DOMAIN)
    return match.group() if keep else EMAIL


def _accent_insensitive(text: str) -> str:
    """A regex for `text` that ignores case, accents and the đ / dj / d spelling."""
    plain = text.lower().replace("đ", "dj").translate(_UNACCENT)
    parts: list[str] = []
    i = 0
    while i < len(plain):
        if plain.startswith("dj", i):
            parts.append(_DJ)
            i += 2
            continue
        char = plain[i]
        parts.append(_DJ if char == "d" else _LETTER_CLASSES.get(char, re.escape(char)))
        i += 1
    return "".join(parts)


def _known_name_re(token: str) -> re.Pattern[str]:
    """A name from the order form, with Bosnian endings.

    Up to four more letters cover endings that add to the name (Ivan -> Ivanu, Perić ->
    Perićeva). A name ending in a vowel changes it (Pero -> Peru, Pera), so when at least
    three letters stay, the last vowel may be any vowel.
    """
    if token[-1].lower() in _VOWELS and len(token) >= 4:
        stem = _accent_insensitive(token[:-1])
        return re.compile(rf"(?i)\b{stem}[{_VOWELS}]\w{{0,3}}\b")
    return re.compile(rf"(?i)\b{_accent_insensitive(token)}\w{{0,4}}\b")


def _known_address_re(address: str) -> re.Pattern[str]:
    parts = (_accent_insensitive(part) for part in address.split())
    return re.compile(r"(?i)" + r"[\s,]+".join(parts))
