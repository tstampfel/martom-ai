"""Strip personal data from a real chat before it is kept longer than the chats (#129).

Real customer chats are never committed to the public repo, even scrubbed (Tomislav,
28 Nov 2026): a scrubbed real chat is exported only to a private location (an S3 bucket
in our AWS account or a private repo), never to `evals/`, which holds only hand-written
or hand-reviewed cases that Tomislav or Toni approved, rewritten so no real customer
wording, street or village remains. Every path that copies a customer chat into
something kept longer than the chats themselves (a LangSmith dataset, a feedback
example or a private export, `martom export-chats` in `export.py`) must still pass it
through `scrub_personal_data` first. `address_matches` exposes the address patterns to the
privacy guard on `evals/`, and `introduced_names` the intro patterns to the export.
It replaces:

- phone numbers with "[telefon]": BiH and HR numbers with a leading 0, "(061)", "+387",
  "00387" or "387", grouped with spaces, "/", "-" or "."; and a number without its 0
  right after a phone word ("moj broj je ...", "broj telefona", "tel:", "mob.",
  "kontakt"), or after a bare "broj" only when its digits are grouped like a phone
  ("61 000 000").
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

Known misses, so the human reading a scrubbed chat knows what to look for: a name the
customer gives without an intro and without the order form ("Pozdrav, Pero"), a street
with no label, street word or delivery verb, even with a house number ("Izmišljena 12,
Čitluk"), a phone without its 0 written in one run after a bare "broj" (it looks like a
listing id), a house number followed by a sentence end and a name that is also a month
("Izmišljena 5. Maja ..." reads as a date), foreign phone numbers, and numbers written
as words. Pass the order form's values whenever there is one, and still read a scrubbed
chat before it is exported or uploaded (`tests/test_fixture_privacy.py` keeps guarding
the committed files).

Known over-scrubs (accepted: the function only ever removes text): a size written in
capitals after a delivery place or street, "DOSTAVA U MOSTAR 20 M OGRADE" ->
"DOSTAVA U [adresa] OGRADE" (a capital M, L or H after a number reads as a building
letter, so "Izmišljena 5 L Mostar" never leaks), and a slash size with no unit after a
delivery place, "dostava na gradilište 50/50" -> "dostava na [adresa]".
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
    r"|tel\.?|telefon|telefona|mob\.?|mobitel|mobitela|mobilni|kontakt)\s*:?\s*)"
    rf"([1-9]\d(?:{_SEP}\d){{5,8}})(?!\w)"
)
# After a bare "broj" the number may be a listing id (7-9 digits, R2-1), so
# only digits grouped the way phones are written count ("broj je 61 000 000"), and never
# after a listing word ("artikal broj ...", "oglas broj ...", "šifra ...").
_PHONE_AFTER_BROJ_RE = re.compile(
    r"(?i)(?:\b(artikal|artikla|artiklu|artikli|artikle|artikala|artiklom"
    r"|oglas|oglasa|oglasu|oglasi|oglase|oglasom|oglasima"
    r"|[sš]ifra|[sš]ifre|[sš]ifru|[sš]ifri|[sš]ifrom)\s+)?"
    r"(\bbroj(?:\s+je)?\s*:?\s*)"
    r"([1-9]\d{1,2}(?:(?:\s?[/.-]\s?|\s)\d{2,4}){2,3})(?!\w)"
)
# Day, month and year: never a phone, however it is separated.
_DATE_RE = re.compile(
    r"\d{1,2}\s?([./-])\s?\d{1,2}\s?\1\s?\d{2,4}"  # 01/12/2026, 01.12.2026
    # 03 12 2026: with spaces only a real day, month and year count, so a phone grouped
    # 2-2-4 ("01 31 0000") is still a phone.
    r"|(?:0?[1-9]|[12]\d|3[01])\s(?:0?[1-9]|1[0-2])\s(?:19|20)\d{2}"
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
# What may follow a number without it being a house number. Every word is a whole word
# in its real forms, never an open prefix (R3-1: "dan\\w*" swallowed "danas", "mart"
# swallowed "Martom", so the address before them leaked). `(?!\\w)` closes each form, so
# "%" and "\u00d7" work too.
_UNIT_WORDS = (
    "cm mm m km kg l h x \u00d7 % kom kom. komad komada komadi komadu komade "
    "metar metra metri metara metru metrima metre "
    "stub stuba stubu stubovi stubova stubove stubovima "
    "panel panela panelu paneli panele panelima "
    "paleta palete paletu paleti paletama col cola coli "
    # Times: "dostava u Mostar 3 dana", "za 2 sata".
    "dan dana danu dani danima sat sata satu sati satima min minut minuta minute minutu "
    "sedmica sedmice sedmicu sedmici sedmica sedmicama "
    "tjedan tjedna tjednu tjedni tjedana tjednima"
)
# After "15." only a month (a date), so "7. Hvala", "7. Martom", "9. Julija" stay
# house numbers.
_MONTH_WORDS = (
    "januar januara januaru februar februara februaru mart marta martu april aprila aprilu "
    "maj maja maju jun juna junu juni jul jula julu juli august augusta augustu avgust "
    "avgusta avgustu septembar septembra septembru oktobar oktobra oktobru novembar "
    "novembra novembru decembar decembra decembru "
    "siječanj siječnja siječnju sijecanj sijecnja sijecnju veljača veljače veljači veljaca "
    "veljace veljaci ožujak ožujka ožujku ozujak ozujka ozujku travanj travnja travnju "
    "svibanj svibnja svibnju lipanj lipnja lipnju srpanj srpnja srpnju kolovoz kolovoza "
    "kolovozu rujan rujna rujnu listopad listopada listopadu studeni studenog studenoga "
    "studenom prosinac prosinca prosincu"
)


def _whole_words(words: str) -> str:
    # Longest first, so "komada" is tried before "kom"; escaped for "kom." and "%".
    forms = sorted(set(words.split()), key=len, reverse=True)
    return "(?:" + "|".join(re.escape(form) for form in forms) + ")(?!\\w)"


_NOT_HOUSE_NUMBER = (
    rf"(?!\s*(?i:{_whole_words(_UNIT_WORDS)})"
    # "15. decembra" (a date), "54,90" (a price), "153x250" (a size: a digit must
    # follow, so "5 xanadu" stays a house number). What is left after a shorter house
    # number is a count or a size only when it looks like one: a range with a unit
    # ("5-7 dana"), a slash part of 3+ digits ("153/250", "60/40/2000") or a slash part with a unit
    # ("60/40 cm"). Anything else ("5-12a") keeps the house number, so the street goes.
    rf"|\.\s*(?i:{_whole_words(_MONTH_WORDS)})|[,.]\d|\s*[x\u00d7*]\s*\d"
    rf"|\s?[/\u2013-]\s?\d+\s*(?i:{_whole_words(_UNIT_WORDS)})|(?:\s?/\s?\d+)*?\s?/\s?\d{{3}})"
)
# "bb" (bez broja), also written "b.b." or "B.B.".
_BB = r"(?:b\.\s?b\.?|bb)(?!\w)"
# "5", "5a", "5 A", "5-7", and up to two entrance or flat parts after a slash (R4-1):
# "24/14", "7 / 3", "7/III", "24/14a", "12/4/2". A slash part longer than 2 digits
# ("153/250", "60/40/2000") is a size; "60/40 cm" is one through the unit word after it.
# A one-digit number takes a 3-digit flat ("7/123"); a size has a longer first part.
_HOUSE_RANGE = r"(?:\s?[\u2013-]\s?\d{1,4}[a-zA-Z]?(?!\w))?"
_HOUSE_NUMBER = (
    r"(?:(?:\d\s?/\s?\d{3}[a-zA-Z]?(?!\w)"
    # "5 A": a spaced letter is upper case only, never "X" ("12 X 3" is a size). Every
    # regex using this is case-insensitive, so the case is pinned: "20 m ograde" is a
    # length (R5-1). A capital M, L or H is a building letter too, whatever follows
    # (Tomislav: fail closed, so "Izmišljena 5 L Mostar" never leaks); the cost is the
    # over-scrub of an all-caps size after a place ("MOSTAR 20 M OGRADE").
    r"|\d{1,4}(?:[a-zA-Z](?!\w)|\s(?-i:(?!X)[A-Z])(?!\w))?"
    rf"{_HOUSE_RANGE}"
    r"(?:\s?/\s?(?:\d{1,2}|[IVX]{1,4}|[ivx]{1,4})[a-zA-Z]?(?!\w)){0,2})"
    rf"{_HOUSE_RANGE}(?!\w){_NOT_HOUSE_NUMBER}|{_BB})"
)
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
    r"(?i)\b(?!(?:je|na|u|i|za|do|od|sa|s|a)\s)" rf"{_STREET_WORDS}{{1,2}}{_BB}"
)
# Postal code and town: "88000 Mostar", after a comma or at the start of a line (as in
# "..., 88000 Mostar"), so "Kod 12345 Panel" stays.
_POSTAL_RE = re.compile(rf"(?m)(?:(?<=,)|(?<=, )|^)\d{{5}}\s+{_CAPITALISED}")

# The address patterns that need no order form, in the order the scrub applies them after
# the label: (pattern, keeps its first group). `address_matches` reuses them, so the
# privacy guard on `evals/` (tests/test_fixture_privacy.py) and the scrub can't drift.
_CONTEXT_ADDRESS_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (_STREET_KEYWORD_RE, False),
    (_STREET_AFTER_VERB_RE, True),
    (_BB_STREET_RE, False),
    (_POSTAL_RE, False),
)

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


def address_matches(text: str) -> list[str]:
    """The addresses in `text` that the scrub would replace without an order form.

    A labelled address ("Adresa: ..."), a street word with a house number, a street after
    a delivery or living verb, a street ending in "bb" and a postal code with its town,
    found by the same patterns `scrub_personal_data` uses (only the address part, never
    the label or the verb). Used by the privacy guard on the committed eval files (#158).
    """
    found = [match.group(0)[len(match.group(1)) :] for match in _ADDRESS_LABEL_RE.finditer(text)]
    for pattern, keeps_prefix in _CONTEXT_ADDRESS_PATTERNS:
        for match in pattern.finditer(text):
            prefix = match.group(1) if keeps_prefix else ""
            found.append(match.group(0)[len(prefix) :])
    return [address for address in found if address.strip() and address.strip() != ADDRESS]


def introduced_names(text: str) -> list[str]:
    """The names a customer introduces in `text` ("Zovem se Pero Perić", "Ime: Ana").

    A whole chat is scrubbed with these as `names`, so the advisor's "Pero, ..." two
    turns later goes too, not only the intro itself (#158, the private chat export).

    Every word returned is matched in every message, so only words that look like a name
    are kept (R1-2): the intro's first word (customers type "zovem se pero" in lower
    case), then the capitalised words right after it, each of 3+ letters. "Zovem se
    Ivan zanima me cijena" gives "Ivan", never "zanima me", and "Ime: Marko Marić trebam
    panel 153 cm" gives "Marko Marić", so "panel" and "153 cm" survive elsewhere.
    """
    found: list[str] = []
    for pattern in (_INTRO_RE, _JA_SAM_RE, _NAME_LABEL_RE):
        for match in pattern.finditer(text):
            words = match.group(0)[len(match.group(1)) :].split()
            name: list[str] = []
            for word in words[:3]:
                word = word.strip(".,;:!?")
                plain_word = re.fullmatch(r"[^\W\d_]{3,}", word) is not None
                if not plain_word or (name and not word[0].isupper()):
                    break
                name.append(word)
            if name:
                found.append(" ".join(name))
    return found


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
    for pattern, keeps_prefix in _CONTEXT_ADDRESS_PATTERNS:
        text = pattern.sub(rf"\g<1>{ADDRESS}" if keeps_prefix else ADDRESS, text)
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
    # A phone word says what the number is, so no date exemption here.
    prefix, number = match.group(1), match.group(2)
    if _is_shop_phone(number):
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
