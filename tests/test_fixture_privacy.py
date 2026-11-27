"""Privacy guard for committed fixtures (M2-1b).

The repo is public, and `tests/fixtures/` and `evals/` hold real OLX listing text. This guard
scans every file under them and fails on what must never be committed: phone numbers,
email addresses, seller/user fields, oversized files and files with more than 20 listings.

Only Martom's public contacts pass: the shop number 036 651 427, the martom-bekaert.com
addresses, the fake 060 000 000 that stands in for private numbers, and Martom's own public
OLX shop identity (user id 17193, username "martom", type "shop").
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOTS = (REPO_ROOT / "tests" / "fixtures", REPO_ROOT / "evals")

MAX_FILE_BYTES = 100_000
MAX_LISTINGS_PER_FILE = 20


# Phone numbers as national digits (+387 → 0). The fake one replaces private numbers.
ALLOWED_PHONES = {"036651427", "060000000"}
ALLOWED_EMAIL_DOMAIN = "martom-bekaert.com"
# Martom's own public OLX shop identity, which GET /listings/{id} sends as the owner.
MARTOM_SHOP_VALUES = {"17193", "martom", "shop"}

# Bosnian numbers: area code 03x-06x (landlines 03x-05x, mobiles 06x), then 6-7 digits, with
# optional single separators. The digit lookarounds keep long ids and timestamps out.
_DIGITS = r"(?:[ ./-]?\d){6,7}(?!\d)"
PHONE_RE = re.compile(
    rf"(?<![\d+])\(?0[3-6]\d\)?{_DIGITS}"  # national: 063 123 456, (033) 123-456
    rf"|(?<!\d)(?:\+|00) ?387[ ./-]?(?:\(0\))?[ ./-]?[3-6]\d{_DIGITS}"  # +387 61 123 456
)
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")

# A key is a seller/user field when one of its words (split on "_", "-" or camelCase) is one
# of these: user, user_id, sellerName, contact_phone, ...
SELLER_KEY_WORDS = {
    "user", "username", "seller", "owner", "phone", "mobile", "tel", "telephone",
    "contact", "contacts", "email", "mail", "viber", "whatsapp",
}  # fmt: skip

# A JSON object is a listing when it has an id and listing text, or a listing_id; an object
# keyed by an OLX listing id (6+ digits) is one listing too. Id-only pagination rows are not.
LISTING_TEXT_KEYS = {"title", "description", "short_description", "search_text"}
LISTING_ID_KEY_RE = re.compile(r"\d{6,}")


def normalise_phone(match: str) -> str:
    digits = re.sub(r"\D", "", match)
    for prefix in ("00387", "387"):
        if digits.startswith(prefix):
            return "0" + digits.removeprefix(prefix).removeprefix("0")
    return digits


def text_problems(text: str, where: str) -> list[str]:
    """Phone numbers and emails in a piece of text, except Martom's public ones."""
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
    text = str(leaf).strip()
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
            if is_seller_key(key) and not all(is_martom_value(leaf) for leaf in leaves(item)):
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
            if LISTING_ID_KEY_RE.fullmatch(key) and isinstance(item, dict):
                ids.add(key)
            ids |= listing_ids(item)
    elif isinstance(value, list):
        for item in value:
            ids |= listing_ids(item)
    return ids


def parse_json_documents(path: Path, text: str) -> list[object]:
    """A .json file is one document; a .jsonl file is one document per non-empty line."""
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return [json.loads(text)]


def scan_file(path: Path) -> list[str]:
    """Everything in one fixture file that must not be committed (empty list = clean)."""
    name = path.name
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return [f"{name}: {size} bytes, over the {MAX_FILE_BYTES}-byte cap"]
    text = path.read_text(encoding="utf-8")
    if path.suffix not in (".json", ".jsonl"):
        return text_problems(text, name)
    try:
        documents = parse_json_documents(path, text)
    except json.JSONDecodeError as error:
        return [f"{name}: invalid JSON ({error}), so it can't be checked"]
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
    ],
)
def test_planted_phone_number_fails(tmp_path: Path, number: str) -> None:
    path = write_json(tmp_path, {"listings": [listing(63996799, f"Pozovite {number} Ivo")]})

    problems = scan_file(path)

    assert len(problems) == 1
    assert "phone" in problems[0]


@pytest.mark.parametrize(
    "number", ["036 651 427", "036/651-427", "+387 36 651 427", "060 000 000", "060000000"]
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
