"""Privacy guard for committed fixtures (M2-1b).

The repo is public, and `tests/fixtures/` and `evals/` hold real OLX listing text. This guard
scans every file under them and fails on what must never be committed: phone numbers,
email addresses, seller/user fields, oversized files and files with more than 20 listings.

Only Martom's public contacts pass: the shop number 036 651 427, the martom-bekaert.com
addresses, the fake 060 000 000 that stands in for private numbers, and Martom's own public
OLX shop identity (user id 17193, username "martom", type "shop").

It scans every file on disk under those folders, untracked and git-ignored ones included, so
a problem shows up before the file is committed. JSON, JSONL, YAML and CSV files get the
seller-field and listing checks; other text files get the phone and email scan. Binary files
(images) and `.DS_Store` are skipped.
"""

import csv
import io
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOTS = (REPO_ROOT / "tests" / "fixtures", REPO_ROOT / "evals")

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

# Files the guard skips by name (Finder metadata, created by macOS in any folder).
IGNORED_NAMES = {".DS_Store"}

# Unicode hyphens and dashes (U+2010-U+2015) and the minus sign (U+2212), all read as "-".
_DASHES = dict.fromkeys([*range(0x2010, 0x2016), 0x2212], "-")


def normalise_text(text: str) -> str:
    """Make separators uniform before matching phone numbers.

    `&nbsp;`, NBSP and every other Unicode space become one plain space, Unicode dashes
    become "-", and "(0)" / brackets around an area code are dropped.
    """
    text = re.sub(r"&nbsp;", " ", text, flags=re.IGNORECASE)
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


def text_problems(text: str, where: str) -> list[str]:
    """Phone numbers and emails in a piece of text, except Martom's public ones."""
    text = normalise_text(text)
    problems = [
        f"{where}: phone number {match.group()!r}"
        for match in PHONE_RE.finditer(text)
        if normalise_phone(match.group()) not in ALLOWED_PHONES
    ]
    problems += [
        f"{where}: email {match.group()!r}"
        for match in EMAIL_RE.finditer(text)
        if match.group(1).lower() != ALLOWED_EMAIL_DOMAIN
    ]
    return problems


def is_seller_key(key: str) -> bool:
    words = re.sub(r"([a-z])([A-Z])", r"\1_\2", key).lower()
    return any(word in SELLER_KEY_WORDS for word in re.split(r"[_\W]+", words))


def leaves(value: object) -> Iterator[object]:
    """Every scalar inside a JSON value (strings, numbers, booleans, null)."""
    if isinstance(value, dict):
        for item in value.values():
            yield from leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from leaves(item)
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


def json_problems(value: object, where: str) -> list[str]:
    """Walk a JSON value: phones and emails in strings, and seller/user fields."""
    if isinstance(value, str):
        return text_problems(value, where)
    problems: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{where}.{key}"
            if is_seller_key(str(key)) and not all(is_martom_value(leaf) for leaf in leaves(item)):
                problems.append(f"{path}: seller/user field {item!r}")
            else:
                problems += json_problems(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            problems += json_problems(item, f"{where}[{index}]")
    return problems


def listing_ids(value: object) -> set[str]:
    """The distinct OLX listings a JSON value holds (see LISTING_TEXT_KEYS)."""
    ids: set[str] = set()
    if isinstance(value, dict):
        if "listing_id" in value:
            ids.add(str(value["listing_id"]))
        elif "id" in value and LISTING_TEXT_KEYS & value.keys():
            ids.add(str(value["id"]))
        for key, item in value.items():
            if LISTING_ID_KEY_RE.fullmatch(str(key)) and isinstance(item, dict):
                ids.add(key)
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
    match path.suffix:
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


def scan_file(path: Path) -> list[str]:
    """Everything in one fixture file that must not be committed (empty list = clean).

    Binary files (a NUL byte in the first 8 KB, e.g. images) are skipped; text that isn't
    UTF-8 is reported, because it can't be checked.
    """
    name = path.name
    if name in IGNORED_NAMES:
        return []
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return [f"{name}: {size} bytes, over the {MAX_FILE_BYTES}-byte cap"]
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [f"{name}: not UTF-8 text, so it can't be checked"]
    try:
        documents = parse_documents(path, text)
    except json.JSONDecodeError as error:
        return [f"{name}: invalid JSON ({error}), so it can't be checked"]
    except yaml.YAMLError as error:
        return [f"{name}: invalid YAML ({error}), so it can't be checked"]
    except csv.Error as error:
        return [f"{name}: invalid CSV ({error}), so it can't be checked"]
    if documents is None:
        return text_problems(text, name)
    problems: list[str] = []
    ids: set[str] = set()
    for document in documents:
        problems += json_problems(document, name)
        ids |= listing_ids(document)
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
    ["info@martom-bekaert.com", "zeljezarija@martom-bekaert.com", "INFO@Martom-Bekaert.com"],
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


def test_binary_file_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")

    assert scan_file(path) == []


def test_ds_store_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xff\xfe")

    assert scan_file(tmp_path / ".DS_Store") == []


def test_non_utf8_text_fails(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    path.write_bytes("id,text\n1,Čitluk 063 123 456\n".encode("cp1250"))

    problems = scan_file(path)

    assert len(problems) == 1
    assert "UTF-8" in problems[0]
