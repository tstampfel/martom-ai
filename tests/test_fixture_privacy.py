"""Privacy guard for committed fixtures (M2-1b).

The repo is public, and `tests/fixtures/` and `evals/` hold real OLX listing text (and
`knowledge/sources/` Toni's raw Q&A, #169). This guard
scans every file under them and fails on what must never be committed: phone numbers,
email addresses, seller/user fields, oversized files and files with more than 20 listings.

Only Martom's public contacts pass: the shop number 036 651 427, the martom-bekaert.com
addresses, the fake 060 000 000 that stands in for private numbers, and Martom's own public
OLX shop identity (user id 17193, username "martom", type "shop").

It scans every file on disk under those folders, untracked and git-ignored ones included, so
a problem shows up before the file is committed. Every text file (UTF-8 or UTF-16) gets the
phone and email scan over its raw, HTML-unescaped and URL-decoded text; JSON, JSONL, YAML and
CSV files are also parsed for the seller-field and listing checks. Binary files (images,
fonts) are skipped, but a test fails if any sits under the fixture roots; `.DS_Store` is
ignored.
"""

import codecs
import csv
import html
import io
import json
import re
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# knowledge/sources/ holds Toni's raw Q&A files (#169), committed next to the chunks made from them.
FIXTURE_ROOTS = (
    REPO_ROOT / "tests" / "fixtures",
    REPO_ROOT / "evals",
    REPO_ROOT / "knowledge" / "sources",
)

MAX_FILE_BYTES = 100_000
MAX_LISTINGS_PER_FILE = 20


# Phone numbers in E.164 form. The fake 060 000 000 replaces private numbers in fixtures.
ALLOWED_PHONES = {"+38736651427", "+38760000000"}
ALLOWED_EMAIL_DOMAIN = "martom-bekaert.com"
# Martom's own public OLX shop identity, which GET /listings/{id} sends as the owner.
MARTOM_SHOP_VALUES = {"17193", "martom", "shop"}

# Phone numbers, matched on normalised text (see normalise_text): up to 3 separator characters
# between digits, so "063 / 123 - 456" counts. The digit lookarounds keep long ids, EANs and
# timestamps out.
_SEP = r"[ ./_-]{0,3}"
PHONE_RE = re.compile(
    # international, any country: +385 91 123 4567, 0049 151 12345678
    rf"(?<![\d+])(?:\+|00){_SEP}[1-9](?:{_SEP}\d){{6,13}}(?!\d)"
    # BiH or Croatian country code without the plus: 387 63 123 456
    rf"|(?<![\d+])38[57](?:{_SEP}\d){{8,9}}(?!\d)"
    # national, BiH (03x-06x) and Croatia (01, 02x, 09x): 063 123 456, 091 123 4567
    rf"|(?<![\d+])0[1-9](?:{_SEP}\d){{7,8}}(?!\d)"
)
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")

# A key is a seller/user field when one of its words (split on "_", "-" or camelCase) is one
# of these: user, user_id, sellerName, customer_name, kupac, ...
SELLER_KEY_WORDS = {
    "user", "username", "seller", "owner", "phone", "mobile", "tel", "telephone",
    "contact", "contacts", "email", "mail", "viber", "whatsapp", "customer", "buyer",
    "author",
    # Bosnian: customer, seller, name, phone, contact, user, owner
    "kupac", "prodavac", "ime", "telefon", "kontakt", "korisnik", "vlasnik",
}  # fmt: skip

# A JSON object is a listing when it has an id and listing text, or a listing_id; an object
# keyed by an OLX listing id (6+ digits) is one listing too. Id-only pagination rows are not.
LISTING_TEXT_KEYS = {"title", "description", "short_description", "search_text"}
LISTING_ID_KEY_RE = re.compile(r"\d{6,}")

# Files the guard skips by name: Finder metadata, created by macOS in any folder and
# git-ignored, so it is never committed.
IGNORED_NAMES = {".DS_Store"}
# Binary types the guard can't read. scan_file skips them, and a test insists none sit under
# the fixture roots, so a skip is never silent.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz",
}  # fmt: skip

# Unicode hyphens and dashes (U+2010-U+2015) and the minus sign (U+2212), all read as "-".
_DASHES = dict.fromkeys([*range(0x2010, 0x2016), 0x2212], "-")


def normalise_text(text: str) -> str:
    """Make separators uniform before matching phone numbers.

    HTML entities are decoded (`&nbsp;`, `&#160;`, `&#xa0;`), NBSP and every other Unicode
    space become one plain space, Unicode dashes become "-", and "(0)" / brackets around an
    area code are dropped.
    """
    text = html.unescape(text)
    text = text.replace("(0)", " ").replace("(", " ").replace(")", " ").translate(_DASHES)
    return re.sub(r"\s+", " ", text)  # in a str pattern, \s matches every Unicode space


def normalise_phone(match: str) -> str:
    """A matched number in E.164 form: "036 651 427" → "+38736651427".

    A national number (leading 0) is read as a BiH one; the result is only compared with the
    allowlist, so a Croatian national number simply doesn't match it.
    """
    digits = re.sub(r"\D", "", match)
    if match.startswith("+"):
        number = digits
    elif digits.startswith("00"):
        number = digits[2:]
    elif digits.startswith("0"):
        number = "387" + digits[1:]
    else:
        number = digits  # 387... or 385... without the plus
    for country in ("387", "385"):
        if number.startswith(country + "0"):  # +387 061 ... → +387 61 ...
            number = country + number[len(country) + 1 :]
    return "+" + number


def contact_findings(text: str) -> dict[str, str]:
    """Phone numbers and emails in a piece of text, except Martom's public ones.

    Returns {dedup key: message}: the key is the number in E.164 form or the lowercased email,
    so the same contact found twice (in the raw text and in a parsed value, or in both copies
    below) is reported once. The text is scanned as written and URL-decoded ("%20" and "+"
    become spaces), so numbers inside a wa.me link's `?text=` are caught too.
    """
    findings: dict[str, str] = {}
    for copy in (text, urllib.parse.unquote_plus(text)):
        normalised = normalise_text(copy)
        for match in PHONE_RE.finditer(normalised):
            number = normalise_phone(match.group())
            if number not in ALLOWED_PHONES:
                findings.setdefault(number, f"phone number {match.group()!r}")
        for match in EMAIL_RE.finditer(normalised):
            if match.group(1).lower() != ALLOWED_EMAIL_DOMAIN:
                findings.setdefault(match.group().lower(), f"email {match.group()!r}")
    return findings


def is_seller_key(key: str) -> bool:
    words = re.sub(r"([a-z])([A-Z])", r"\1_\2", key).lower()
    return any(word in SELLER_KEY_WORDS for word in re.split(r"[_\W]+", words))


def leaves(value: object) -> Iterator[object]:
    """Every scalar inside a parsed value (strings, numbers, booleans, null)."""
    if isinstance(value, dict):
        for item in value.values():
            yield from leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from leaves(item)
    else:
        yield value


def keys_and_leaves(value: object) -> Iterator[object]:
    """Every dict key and every scalar inside a parsed value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys_and_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys_and_leaves(item)
    else:
        yield value


def is_martom_value(leaf: object) -> bool:
    """True for null/empty, Martom's shop identity, or one of Martom's public contacts."""
    if leaf is None or isinstance(leaf, bool) or leaf == "":
        return True
    text = normalise_text(str(leaf)).strip()
    if text.lower() in MARTOM_SHOP_VALUES:
        return True
    if PHONE_RE.fullmatch(text):
        return normalise_phone(text) in ALLOWED_PHONES
    email = EMAIL_RE.fullmatch(text)
    return email is not None and email.group(1).lower() == ALLOWED_EMAIL_DOMAIN


def seller_field_problems(value: object, where: str) -> list[str]:
    """Seller/user fields anywhere in a parsed value, unless they hold only Martom values."""
    problems: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{where}.{key}"
            if is_seller_key(str(key)) and not all(is_martom_value(leaf) for leaf in leaves(item)):
                problems.append(f"{path}: seller/user field {item!r}")
            else:
                problems += seller_field_problems(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            problems += seller_field_problems(item, f"{where}[{index}]")
    return problems


def listing_ids(value: object) -> set[str]:
    """The distinct OLX listings a parsed value holds (see LISTING_TEXT_KEYS)."""
    ids: set[str] = set()
    if isinstance(value, dict):
        if "listing_id" in value:
            ids.add(str(value["listing_id"]))
        elif "id" in value and LISTING_TEXT_KEYS & value.keys():
            ids.add(str(value["id"]))
        for key, item in value.items():
            if LISTING_ID_KEY_RE.fullmatch(str(key)) and isinstance(item, dict):
                ids.add(str(key))
            ids |= listing_ids(item)
    elif isinstance(value, list):
        for item in value:
            ids |= listing_ids(item)
    return ids


def parse_documents(path: Path, text: str) -> list[object] | None:
    """Parse a structured fixture into JSON-like values; None for a plain-text file.

    .json is one document, .jsonl one per non-empty line, .yaml/.yml one per YAML document,
    and .csv one list of rows (dicts keyed by the header), so seller columns and listing rows
    are checked like JSON keys and objects.
    """
    match path.suffix.lower():
        case ".json":
            return [json.loads(text)]
        case ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        case ".yaml" | ".yml":
            return list(yaml.safe_load_all(text))
        case ".csv":
            return [list(csv.DictReader(io.StringIO(text), restkey="extra_columns"))]
        case _:
            return None


def decode_text(raw: bytes) -> str | None:
    """Decode a text file: UTF-8 (with or without a BOM) or UTF-16 LE/BE; None if neither.

    UTF-16 is recognised by its BOM, or, without one, by its NUL bytes (real UTF-8 text never
    holds NUL): NULs on odd positions mean little-endian ASCII, on even positions big-endian.
    Without a BOM the UTF-16 reading is kept only when NULs fill at least 30% of one byte
    parity and the result looks like text (see is_plausible_text). Any other file holding a
    NUL (a database, a stray NUL in UTF-8, UTF-32 without a BOM) is undecodable, so scan_file
    reports it instead of passing it.
    """
    try:
        if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            return raw.decode("utf-32")
        if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return raw.decode("utf-16")
        if b"\x00" in raw:
            odd_nuls, even_nuls = raw[1::2].count(0), raw[0::2].count(0)
            little_endian = odd_nuls >= even_nuls
            # UTF-16 Latin text has a NUL in (nearly) every other byte; demand at least 30%.
            if max(odd_nuls, even_nuls) < 0.3 * (len(raw) // 2):
                return None
            text = raw.decode("utf-16-le" if little_endian else "utf-16-be")
            return text if is_plausible_text(text) else None
        return raw.decode("utf-8-sig")  # utf-8-sig also reads plain UTF-8, dropping a BOM
    except UnicodeDecodeError:
        return None


def is_plausible_text(text: str) -> bool:
    """True when at least 95% of the characters are printable or whitespace."""
    readable = sum(char.isprintable() or char.isspace() for char in text)
    return bool(text) and readable >= 0.95 * len(text)


def is_binary(path: Path) -> bool:
    return path.suffix.lower() in BINARY_SUFFIXES


def unscannable_files(paths: list[Path]) -> list[Path]:
    """The files scan_file skips as binary, so a test can insist there are none."""
    return [path for path in paths if is_binary(path)]


def scan_file(path: Path) -> list[str]:
    """Everything in one fixture file that must not be committed (empty list = clean).

    Every text file gets the phone and email scan over its raw text, whatever its type, so
    parsing can never hide a number (unquoted YAML ints, CSV headers, JSON keys). JSON, JSONL,
    YAML and CSV are also parsed for the seller-field and listing checks, and their parsed
    keys and values are scanned too (that catches `\\u00a0`-escaped JSON text). Binary files
    (BINARY_SUFFIXES) are skipped here and listed by unscannable_files; text that can't be
    decoded is reported.
    """
    name = path.name
    if name in IGNORED_NAMES or is_binary(path):
        return []
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return [f"{name}: {size} bytes, over the {MAX_FILE_BYTES}-byte cap"]
    text = decode_text(path.read_bytes())
    if text is None:
        return [f"{name}: not UTF-8 or UTF-16 text, so it can't be checked"]
    findings = contact_findings(text)
    try:
        documents = parse_documents(path, text) or []
    except json.JSONDecodeError as error:
        return [f"{name}: invalid JSON ({error}), so it can't be checked"]
    except yaml.YAMLError as error:
        return [f"{name}: invalid YAML ({error}), so it can't be checked"]
    except csv.Error as error:
        return [f"{name}: invalid CSV ({error}), so it can't be checked"]
    structure_problems: list[str] = []
    ids: set[str] = set()
    for document in documents:
        for item in keys_and_leaves(document):
            if item is not None and not isinstance(item, bool):
                for key, message in contact_findings(str(item)).items():
                    findings.setdefault(key, message)
        structure_problems += seller_field_problems(document, name)
        ids |= listing_ids(document)
    problems = [f"{name}: {message}" for message in findings.values()] + structure_problems
    if len(ids) > MAX_LISTINGS_PER_FILE:
        problems.append(f"{name}: {len(ids)} listings, over the cap of {MAX_LISTINGS_PER_FILE}")
    return problems


# --- the committed fixtures ------------------------------------------------------------


def fixture_files() -> list[Path]:
    """Every file on disk under the fixture roots (a missing root, e.g. `evals/`, is fine)."""
    return sorted(
        path
        for root in FIXTURE_ROOTS
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
    )


def test_guard_finds_the_fixtures() -> None:
    assert len(fixture_files()) >= 6


def test_guard_scans_the_knowledge_sources() -> None:
    # Toni's raw Q&A (#169) is committed under knowledge/sources/, so it gets the same scan.
    source = REPO_ROOT / "knowledge" / "sources" / "martom_3d_panels_qa.jsonl"
    assert source in fixture_files()


@pytest.mark.parametrize("path", fixture_files(), ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_committed_fixture_is_clean(path: Path) -> None:
    assert scan_file(path) == []


# --- planted problems ------------------------------------------------------------------


def write_json(tmp_path: Path, data: object, name: str = "fixture.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def listing(listing_id: int, description: str = "Panel 153 cm") -> dict[str, object]:
    return {"id": listing_id, "title": "Panel", "description": description}


@pytest.mark.parametrize(
    "number",
    [
        "063 123 456",
        "061-123-4567",
        "065/123-456",
        "062123456",
        "033 123 456",
        "(033) 123-456",
        "+387 61 123 456",
        "+387 (0)61 123 456",
        "00387 63 123 456",
        "+38733123456",
        # R1-1: separators real OLX text uses (NBSP, runs of spaces, spaced / and -, dashes).
        "063\u00a0123\u00a0456",
        "063&nbsp;123&nbsp;456",
        "063  123 456",
        "063 / 123 456",
        "063 - 123 - 456",
        "063\u2013123\u2013456",
        "063_123_456",
        "063\u2009123\u2009456",
        "387 63 123 456",
        # R1-2: Croatian numbers (the private ORDER_PHONE is +385) and other countries.
        "+385 91 123 4567",
        "00385 91 123 4567",
        "+385 (0)98 123 456",
        "091 123 4567",
        "098/123-456",
        "01 234 5678",
        "020 123 456",
        "+49 151 12345678",
        "+1 212 555 0100",
        "+385 36 651 427",
    ],
)
def test_planted_phone_number_fails(tmp_path: Path, number: str) -> None:
    path = write_json(tmp_path, {"listings": [listing(63996799, f"Pozovite {number} Ivo")]})

    problems = scan_file(path)

    assert len(problems) == 1
    assert "phone" in problems[0]


@pytest.mark.parametrize(
    "number",
    [
        "036 651 427",
        "036/651-427",
        "+387 36 651 427",
        "00387 36 651 427",
        "036\u00a0651\u00a0427",
        "036 / 651 - 427",
        "060 000 000",
        "060000000",
        "+387 60 000 000",
    ],
)
def test_martom_and_fake_numbers_pass(tmp_path: Path, number: str) -> None:
    path = write_json(tmp_path, {"listings": [listing(63996799, f"Kontakt: {number}")]})

    assert scan_file(path) == []


@pytest.mark.parametrize(
    "text",
    [
        "facebook.com/132087646846187",
        "flex-20260925012431-xJQqRKKPH.jpg",
        "listing 63996799",
        "Cijena 0.5 KM, 06.11.2026",
        "150 x 250 cm, 3 mm",
        # Separator forms found in the committed fixtures (NBSP, double spaces).
        "Debljina žice 4,50mm\u00a0 i",
        "2500 x 1230\u00a0 mm",
        "030  mm",
        "Cijena 1.030,00 KM, 45,00 KM",
        "EAN 0123456789012",
        "30.06.2026. i 04/05/2026 i 2026-05-04",
        "0.50 x 2.00 m, 153 X 2500CM",
    ],
)
def test_long_numbers_ids_and_dates_are_not_phones(tmp_path: Path, text: str) -> None:
    path = write_json(tmp_path, {"listings": [listing(63996799, text)]})

    assert scan_file(path) == []


def test_planted_email_fails(tmp_path: Path) -> None:
    path = write_json(tmp_path, {"listings": [listing(63996799, "Pišite na ivo.ivic@gmail.com")]})

    problems = scan_file(path)

    assert len(problems) == 1
    assert "email" in problems[0]


@pytest.mark.parametrize(
    "email",
    ["info@martom-bekaert.com", "prodaja@martom-bekaert.com", "INFO@Martom-Bekaert.com"],
)
def test_martom_emails_pass(tmp_path: Path, email: str) -> None:
    path = write_json(tmp_path, {"listings": [listing(63996799, f"E-mail: {email}")]})

    assert scan_file(path) == []


def test_lookalike_martom_domain_fails(tmp_path: Path) -> None:
    path = write_json(tmp_path, {"note": "ivo@martom-bekaert.com.evil.ba"})

    assert len(scan_file(path)) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"seller": {"name": "Ivo Ivić"}},
        {"user": {"id": 99, "type": "private", "username": "ivo"}},
        {"user_id": 99},
        {"contact": "Ivo"},
        {"details": {"1": {"phone": "nazovi"}}},
        {"sellerName": "Ivo"},
        {"owner": None, "email": "nema"},
        {"customer_name": "Ivo Ivić"},
        {"buyer": "Ivo"},
        {"author": "Ivo"},
        {"kupac": "Ivo"},
        {"prodavac": "Ivo"},
        {"ime": "Ivo"},
        {"telefon": "nazovi"},
        {"kontakt": "Ivo"},
        {"korisnik": "ivo"},
        {"vlasnik": "Ivo"},
    ],
)
def test_planted_seller_or_user_field_fails(tmp_path: Path, data: object) -> None:
    problems = scan_file(write_json(tmp_path, data))

    assert problems
    assert all("field" in problem for problem in problems)


@pytest.mark.parametrize(
    "data",
    [
        {"user": {"id": 17193, "type": "shop", "username": "Martom"}},
        {"user_id": 17193, "user_type": "shop", "username": "martom"},
        {"user_id": "17193"},
        {"user": None},
        {"phone": "036 651 427", "email": "info@martom-bekaert.com"},
    ],
)
def test_martoms_own_shop_fields_pass(tmp_path: Path, data: object) -> None:
    assert scan_file(write_json(tmp_path, data)) == []


def test_jsonl_lines_are_scanned_as_json(tmp_path: Path) -> None:
    path = tmp_path / "seed.jsonl"
    path.write_text('{"question": "ograda"}\n{"seller": "Ivo"}\n', encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "seller" in problems[0]


def test_plain_text_files_are_scanned(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    path.write_text("id,text\n1,zovi 063 123 456\n", encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "phone" in problems[0]


def test_invalid_json_fails(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"id": 1,', encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "JSON" in problems[0]


def test_file_over_the_size_cap_fails(tmp_path: Path) -> None:
    path = tmp_path / "big.txt"
    path.write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "bytes" in problems[0]


def test_file_at_the_size_cap_passes(tmp_path: Path) -> None:
    path = tmp_path / "big.txt"
    path.write_text("x" * MAX_FILE_BYTES, encoding="utf-8")

    assert scan_file(path) == []


def numbered_listings(count: int) -> Iterator[int]:
    return iter(range(10_000_001, 10_000_001 + count))


def test_more_than_20_listing_objects_fail(tmp_path: Path) -> None:
    data = {"listings": [listing(i) for i in numbered_listings(MAX_LISTINGS_PER_FILE + 1)]}

    problems = scan_file(write_json(tmp_path, data))

    assert len(problems) == 1
    assert "21 listings" in problems[0]


def test_20_listings_pass(tmp_path: Path) -> None:
    data = {"listings": [listing(i) for i in numbered_listings(MAX_LISTINGS_PER_FILE)]}

    assert scan_file(write_json(tmp_path, data)) == []


def test_listings_keyed_by_id_count_too(tmp_path: Path) -> None:
    ids = numbered_listings(MAX_LISTINGS_PER_FILE + 1)
    data = {"responses": {str(i): {"product_type": "panel"} for i in ids}}

    problems = scan_file(write_json(tmp_path, data))

    assert len(problems) == 1
    assert "21 listings" in problems[0]


def test_listings_records_with_listing_id_count_too(tmp_path: Path) -> None:
    ids = numbered_listings(MAX_LISTINGS_PER_FILE + 1)
    data = {"records": [{"listing_id": i, "search_text": "panel"} for i in ids]}

    assert len(scan_file(write_json(tmp_path, data))) == 1


def test_same_listing_in_two_places_counts_once(tmp_path: Path) -> None:
    ids = list(numbered_listings(MAX_LISTINGS_PER_FILE))
    data = {
        "listings": {str(i): listing(i) for i in ids},
        "records": [{"listing_id": i, "search_text": "panel"} for i in ids],
    }

    assert scan_file(write_json(tmp_path, data)) == []


def test_id_only_pagination_rows_are_not_listings(tmp_path: Path) -> None:
    rows = [{"id": i, "category_id": 1126} for i in numbered_listings(80)]
    data = {"plain_pagination": {"7": {"data": rows}, "8": {"data": []}}}

    assert scan_file(write_json(tmp_path, data)) == []


def test_csv_seller_column_fails(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    path.write_text("id,title,seller\n1,Panel,Ivo Ivić\n", encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "seller" in problems[0]


def test_csv_over_the_listing_cap_fails(tmp_path: Path) -> None:
    rows = "".join(f"{i},Panel\n" for i in numbered_listings(MAX_LISTINGS_PER_FILE + 1))
    path = tmp_path / "rows.csv"
    path.write_text("id,title\n" + rows, encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "21 listings" in problems[0]


def test_yaml_seller_field_fails(tmp_path: Path) -> None:
    path = tmp_path / "seed.yaml"
    path.write_text("question: ograda\nseller: Ivo Ivić\n", encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "seller" in problems[0]


def test_invalid_yaml_fails(tmp_path: Path) -> None:
    path = tmp_path / "seed.yml"
    path.write_text("a: [1,\n", encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "YAML" in problems[0]


def test_binary_file_is_skipped_but_listed(tmp_path: Path) -> None:
    path = tmp_path / "photo.PNG"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")

    assert scan_file(path) == []
    assert unscannable_files([path, write_json(tmp_path, {})]) == [path]


def test_no_fixture_is_skipped_as_binary() -> None:
    """A binary fixture can't be checked, so none may sit under the fixture roots."""
    assert unscannable_files(fixture_files()) == []


def test_ds_store_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xff\xfe")

    assert scan_file(tmp_path / ".DS_Store") == []


def test_non_utf8_text_fails(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    path.write_bytes("id,text\n1,Čitluk 063 123 456\n".encode("cp1250"))

    problems = scan_file(path)

    assert len(problems) == 1
    assert "UTF-8" in problems[0]


# --- R2: numbers outside string values, other encodings, encoded text ------------------


@pytest.mark.parametrize(
    "text",
    ["broj: +38763123456\n", "napomena: 061234567\n"],  # PyYAML reads both as ints
)
def test_unquoted_yaml_number_fails(tmp_path: Path, text: str) -> None:
    path = tmp_path / "seed.yaml"
    path.write_text(text, encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "phone" in problems[0]


def test_json_number_phone_fails(tmp_path: Path) -> None:
    problems = scan_file(write_json(tmp_path, {"broj": 38763123456}))

    assert len(problems) == 1
    assert "phone" in problems[0]


def test_email_as_a_json_key_fails(tmp_path: Path) -> None:
    problems = scan_file(write_json(tmp_path, {"ivo@gmail.com": "x"}))

    assert len(problems) == 1
    assert "email" in problems[0]


def test_headerless_csv_fails(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    path.write_text("063 123 456,ivo@gmail.com\n1,Panel\n", encoding="utf-8")

    problems = scan_file(path)

    assert len(problems) == 2
    assert "phone" in problems[0]
    assert "email" in problems[1]


def test_csv_with_bom_over_the_listing_cap_fails(tmp_path: Path) -> None:
    rows = "".join(f"{i},Panel\n" for i in numbered_listings(MAX_LISTINGS_PER_FILE + 1))
    path = tmp_path / "rows.csv"
    path.write_text("id,title\n" + rows, encoding="utf-8-sig")

    problems = scan_file(path)

    assert len(problems) == 1
    assert "21 listings" in problems[0]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_text_is_scanned(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "export.txt"
    path.write_bytes("zovi 063 123 456".encode(encoding))

    problems = scan_file(path)

    assert len(problems) == 1
    assert "phone" in problems[0]


def test_utf16_json_is_parsed(tmp_path: Path) -> None:
    path = tmp_path / "export.json"
    path.write_bytes(json.dumps({"seller": "Ivo"}).encode("utf-16"))

    problems = scan_file(path)

    assert len(problems) == 1
    assert "seller" in problems[0]


def test_clean_utf8_bom_json_passes(tmp_path: Path) -> None:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"title": "Panel"}), encoding="utf-8-sig")

    assert scan_file(path) == []


def test_uppercase_suffix_is_parsed(tmp_path: Path) -> None:
    problems = scan_file(write_json(tmp_path, {"seller": "Ivo"}, name="up.JSON"))

    assert len(problems) == 1
    assert "seller" in problems[0]


@pytest.mark.parametrize(
    "text",
    [
        "https://wa.me/38760000000?text=Ivo%20063%20123%20456",
        "https://wa.me/38760000000?text=Ivo+063+123+456",
        "https://wa.me/385911234567?text=Narud%C5%BEba",
        "https://wa.me/%2B385911234567",
        "zovi 063&#160;123&#160;456",
        "zovi 063&#xa0;123&#xa0;456",
    ],
)
def test_url_encoded_and_entity_numbers_fail(tmp_path: Path, text: str) -> None:
    problems = scan_file(write_json(tmp_path, {"link": text}))

    assert len(problems) == 1
    assert "phone" in problems[0]


def test_allowlisted_wa_me_link_passes(tmp_path: Path) -> None:
    link = "https://wa.me/38760000000?text=Narud%C5%BEba%3A%20panel%20153%20cm%2C%2010%20kom"

    assert scan_file(write_json(tmp_path, {"link": link})) == []


# --- O-1: a NUL byte without a BOM must not pass silently ------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"zovi 063 123 456\x00\n",  # UTF-8 with one stray NUL
        b"SQLite format 3\x00\x10\x00\x01\x01\x00@  \x00\x00\x00\x02zovi 063 123 456",
        "zovi 063 123 456".encode("utf-32-le"),  # UTF-32 without a BOM
    ],
    ids=["stray-nul", "sqlite-like", "utf32-no-bom"],
)
def test_nul_bytes_that_are_not_utf16_text_fail(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "data.db"
    path.write_bytes(raw)

    problems = scan_file(path)

    assert len(problems) == 1
    assert "can't be checked" in problems[0]
