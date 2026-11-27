"""Plain-Python checks that find listing problems for Toni (docs/PLAN.md, "What we learned").

Each check reads one listing (or, for duplicates, the whole catalogue) and returns
`ReviewIssue` values with a Bosnian message. Checks only report: they never change the
listing or the extracted product ("Extraction copies, never fixes"), and they never call a
model. `martom.review.job` saves what they return into `review_issue`.

Sizes and prices are read from the extracted variant fields, which hold the text exactly as
the listing wrote it ("153", "2500CM", "H-85 cm", "44,00 KM").
"""

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from martom.bom.manual_products import MAX_AGE_DAYS, ManualProduct, stale_manual_products
from martom.bom.pricing import PriceMap, PriceProblem, resolve_price
from martom.extract.store import VariantRow

type Priority = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class ReviewIssue:
    """One problem for Toni. Saved as one `review_issue` row, deduplicated by fingerprint."""

    kind: str  # e.g. "implausible_dimension"
    listing_id: int | None  # None for problems that aren't about one listing
    priority: Priority
    message: str  # Bosnian, for Toni
    details: dict[str, Any] = field(default_factory=dict)
    # What makes this issue different from another of the same kind on the same listing
    # (the variant's sizes, the misspelt word, ...), from values the listing wrote, never
    # the model's free-text label. Part of the fingerprint; the message is
    # not, so rewording a message doesn't create a second issue.
    key: str = ""
    # False for an issue that spans several listings (duplicates): its listing_id is only
    # the lowest of them, which changes when that listing goes away, so the fingerprint
    # leaves it out and relies on `key` alone.
    per_listing: bool = True

    @property
    def fingerprint(self) -> str:
        listing = "-" if self.listing_id is None or not self.per_listing else str(self.listing_id)
        raw = f"{self.kind}\x1f{listing}\x1f{self.key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CheckProduct:
    """The extracted product of a listing (from `product` + `product_variant`)."""

    product_type: str
    material: str | None
    variants: Sequence[VariantRow]


@dataclass(frozen=True)
class CheckListing:
    """What the checks read about one active listing."""

    id: int
    slug: str | None
    title: str
    description_text: str
    price: Decimal | None  # OLX's own price field (0 for "Na upit")
    display_price: str | None
    price_by_agreement: bool
    state: str | None  # "new" / "used", as OLX says
    attributes: tuple[tuple[str, str], ...]
    photo_count: int
    product: CheckProduct | None  # None until the listing has been extracted

    @property
    def variants(self) -> Sequence[VariantRow]:
        return self.product.variants if self.product else ()


def format_km(value: Decimal) -> str:
    """44 -> "44,00 KM" (Bosnian decimal comma)."""
    return f"{value:.2f} KM".replace(".", ",")


# --- sizes -----------------------------------------------------------------------------------

# A number as listings write it: "153", "4,20", "4.20", and a dot before exactly three
# digits as a thousands separator ("1.530", "1.250,5"), the same reading as
# `martom.extract.prices.parse_price_km`.
_NUMBER = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
# A unit only counts when it touches the number (an optional space between) and is not the
# start of a longer word: "153 cm" and "50M" are units, "153 maslinasto" and "153 mat"
# are not. Metres are also written out ("20METARA").
_UNIT = r"(mm|cm|metara|metra|metar|m)(?![^\W\d_])"
# Not a digit, and not a dot or comma that continues a number: "2500" in "H.2500CM",
# "v.150 cm" or "dim.153x250" is read, the "5" in "25" or "1.5" is not.
_NOT_IN_A_NUMBER = r"(?<!\d)(?<!\d[.,])"
_NUMBER_AND_UNIT = re.compile(rf"{_NOT_IN_A_NUMBER}({_NUMBER})(?:\s*{_UNIT})?")
_CM_PER_UNIT = {"mm": Decimal("0.1"), "cm": Decimal(1), None: Decimal(1)}  # else metres
_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")


def _to_decimal(digits: str, unit: str | None) -> Decimal:
    # "1.530 mm" is 1530 mm, but "2.500 m" is 2,5 m: nobody writes thousands of metres.
    in_metres = unit not in _CM_PER_UNIT
    if _THOUSANDS.fullmatch(digits) and not (in_metres and "," not in digits):
        digits = digits.replace(".", "")
    return Decimal(digits.replace(",", "."))


def _cm(number: Decimal, unit: str | None) -> Decimal:
    return number * _CM_PER_UNIT.get(unit, Decimal(100))


def _unit_in_label(digits: str, label: str) -> str | None:
    """The unit the label gives this same number, or None.

    The unit is the one written right after the number, or after "A x B" when the number
    is the first of a pair and B ends in mm or cm ("2500 x 1030 mm"; not "150 x 2,5m",
    where the metres belong to B alone). When the number appears more than once and the
    places disagree ("visina 100, okca 100 x 50 mm"), nothing is guessed.
    """
    pattern = re.compile(
        rf"{_NOT_IN_A_NUMBER}{re.escape(digits)}(?![\d.,]*\d)"  # the same number, not part of one
        rf"(?:\s*{_UNIT}|(?:\s*[x\u00d7]\s*(?:{_NUMBER}))+\s*(mm|cm)(?![^\W\d_]))?"
    )
    units = {match.group(1) or match.group(2) for match in pattern.finditer(label.casefold())}
    return units.pop() if len(units) == 1 else None


def _parse_size(text: str | None, label: str = "") -> tuple[Decimal, str | None] | None:
    """(size in cm, unit: "mm", "cm", "m"/"metara"... or None), or None without a number.

    Reads the first number in `text` and the unit right next to it. A bare number takes
    the unit its label gives the same number ("2500" in "2500 x 1030 mm" is mm); with no
    unit anywhere it is read as centimetres, the way fence listings write sizes.
    """
    if not text:
        return None
    match = _NUMBER_AND_UNIT.search(text.casefold())
    if match is None:
        return None
    digits, unit = match.group(1), match.group(2)
    if unit is None and label:
        unit = _unit_in_label(digits, label)
    return _cm(_to_decimal(digits, unit), unit), unit


def size_in_cm(text: str | None, label: str = "") -> Decimal | None:
    """The size in centimetres, or None when the text has no number."""
    parsed = _parse_size(text, label)
    return parsed[0] if parsed else None


# Plausible sizes in cm, by what the product is (product_type contains the word). Panels
# are 83-203 cm high and 200 or 250 cm long (docs/domain/fence-rules.md); the ranges leave
# room for other panel systems without letting a unit slip ("2500CM") through.
_RANGES: tuple[tuple[str, str, Decimal, Decimal], ...] = (
    ("panel", "height", Decimal(50), Decimal(260)),
    ("panel", "length", Decimal(100), Decimal(300)),
    ("stup", "height", Decimal(30), Decimal(350)),
    ("stub", "height", Decimal(30), Decimal(350)),
)
# For any product: nobody writes 10 m or more in centimetres, so a bigger cm value is a
# slipped unit. Lengths in metres ("ROLA 50M") are not affected.
_MAX_CM_WRITTEN_AS_CM = Decimal(1000)
# Panel gates ("panelna kapija", "vrata") are wider than fence panels, so the "panel"
# ranges don't apply to them; the post ranges still do ("stup za kapiju").
_GATE_WORDS = ("kapij", "vrat")
# Rolls and tapes ("pvc traka za panelnu ogradu": 19 cm wide, "35M" long) are not panels,
# so neither their width nor their roll length is held to the "panel" ranges.
_ROLL = re.compile(r"\b(?:trak|rol)")
_FIELD_BS = {"height": "visina", "length": "dužina"}


def _size_problem(product_type: str, field_name: str, text: str, label: str) -> bool:
    parsed = _parse_size(text, label)
    if parsed is None:
        return False
    cm, unit = parsed
    gate = any(word in product_type for word in _GATE_WORDS)
    roll = _ROLL.search(product_type) is not None
    for word, ranged_field, low, high in _RANGES:
        if (gate or roll) and word == "panel":
            continue
        if word in product_type and ranged_field == field_name and not low <= cm <= high:
            return True
    return unit in _CM_PER_UNIT and cm > _MAX_CM_WRITTEN_AS_CM


def check_implausible_dimensions(listing: CheckListing) -> list[ReviewIssue]:
    """Heights and lengths that can't be right, e.g. a panel "153 X 2500CM"."""
    if listing.product is None:
        return []
    product_type = listing.product.product_type.casefold()
    issues = []
    keys = _variant_keys(listing.variants)
    for v, variant_key in zip(listing.variants, keys, strict=True):
        for field_name, text in (("height", v.height), ("length", v.length)):
            if text and _size_problem(product_type, field_name, text, v.label):
                issues.append(
                    ReviewIssue(
                        "implausible_dimension",
                        listing.id,
                        "high",
                        f"Oglas {listing.id}: varijanta „{v.label}“ ima nemoguću mjeru: "
                        f"{_FIELD_BS[field_name]} „{text}“. "
                        "Provjerite broj i jedinicu (cm, mm, m).",
                        {"field": field_name, "value": text, "label": v.label},
                        key=f"{field_name}:{variant_key}",
                    )
                )
    return issues


# --- prices ----------------------------------------------------------------------------------


def check_variant_prices(listing: CheckListing) -> list[ReviewIssue]:
    """Variants with no price, or a price text that isn't one clear number.

    Only in listings that price at least one variant: a listing with no price at all is
    one `price_on_request` issue, not one issue per size.
    """
    if not any(v.price_text for v in listing.variants):
        return []
    issues = []
    keys = _variant_keys(listing.variants)
    for v, variant_key in zip(listing.variants, keys, strict=True):
        if v.price_text is None and v.height:
            issues.append(
                ReviewIssue(
                    "height_without_price",
                    listing.id,
                    "high",
                    f"Oglas {listing.id}: visina „{v.height}“ („{v.label}“) nema cijenu u opisu.",
                    {"height": v.height, "label": v.label},
                    key=variant_key,
                )
            )
        elif v.price_text is None:
            issues.append(
                ReviewIssue(
                    "variant_without_price",
                    listing.id,
                    "high",
                    f"Oglas {listing.id}: varijanta „{v.label}“ nema cijenu u opisu.",
                    {"label": v.label},
                    key=variant_key,
                )
            )
        elif v.price is None:
            issues.append(
                ReviewIssue(
                    "unreadable_price",
                    listing.id,
                    "high",
                    f"Oglas {listing.id}: cijena „{v.price_text}“ za „{v.label}“ nije jedna "
                    "jasna cijena.",
                    {"price_text": v.price_text, "label": v.label},
                    key=variant_key,
                )
            )
    return issues


def _priced(listing: CheckListing) -> list[VariantRow]:
    return [v for v in listing.variants if v.price is not None]


def check_price_on_request(listing: CheckListing) -> list[ReviewIssue]:
    """ "Na upit" with no price anywhere in the description: the advisor can't quote it."""
    # price 0 is how OLX sends "Na upit"; None means the detail wasn't fetched yet.
    on_request = listing.price_by_agreement or listing.price == 0
    if not on_request or _priced(listing):
        return []
    return [
        ReviewIssue(
            "price_on_request",
            listing.id,
            "medium",
            f"Oglas {listing.id}: cijena je „{listing.display_price or 'Na upit'}“ i opis ne "
            "navodi nijednu cijenu, pa savjetnik kupcu ne može reći cijenu.",
            {"display_price": listing.display_price},
        )
    ]


def _size_key(v: VariantRow) -> str | None:
    """The variant's sizes and colour, normalised so "250 cm", "250CM" and "250" compare
    equal; None when the variant has none. Sizes are read like `check_implausible_dimensions`
    reads them (a bare "2500" in "2500 x 1030 mm" is 250 cm). Used to group variants for
    `check_price_conflicts`.
    """
    parts = []
    for text in (v.height, v.length):
        parsed = _parse_size(text, v.label)
        parts.append(str(parsed[0].normalize()) if parsed else _normal_text(text))
    # Other sizes ("100/8/15", "SX8") and colours are compared as text.
    parts += [_normal_text(v.size), _normal_text(v.colour)]
    return "|".join(parts) if any(parts) else None


def _label_tokens(v: VariantRow) -> str:
    return " ".join(_WORD_OR_NUMBER.findall(v.label.casefold()))


def _variant_keys(variants: Sequence[VariantRow]) -> list[str]:
    """A fingerprint key for each variant of one listing, distinct within the listing.

    The key is what the listing wrote in the variant's size, colour and unit fields (case
    and spaces ignored), so a reworded label keeps the fingerprint and Toni's status. Only
    when two variants share that key does it grow, step by step and only for those
    variants: first the price as written, then the label's words (the model writes the
    label, so these keys can change on re-extraction), and last a running number for
    variants that are identical even in their label. A variant with no size, colour or
    unit at all (an accessory such as "Kopča") has only its label to go by.
    """
    keys = []
    for v in variants:
        fields = (v.height, v.length, v.size, v.colour, v.unit)
        base = "|".join(_normal_text(text) for text in fields)
        keys.append(base if any(fields) else "label:" + _label_tokens(v))
    for extra in (lambda v: "price:" + _normal_text(v.price_text), _label_tokens):
        counts = Counter(keys)
        keys = [
            f"{key}|{extra(v)}" if counts[key] > 1 else key
            for key, v in zip(keys, variants, strict=True)
        ]
    seen: Counter[str] = Counter()
    numbered = []
    for key in keys:
        seen[key] += 1
        numbered.append(key if seen[key] == 1 else f"{key}#{seen[key]}")
    return numbered


def _normal_text(text: str | None) -> str:
    """Case and spaces don't matter: "Panel", " panel " and "PANEL" are the same."""
    return "".join(text.casefold().split()) if text else ""


# A price written in a label: "62KM", "7,50 KM", "9,00km/m", "10,00/km/m", "2,50KM/KOM".
# Only a unit after the slash belongs to the price: "62KM/INOX" keeps "INOX".
_PER_UNIT = r"m2|m1|m|kg|kom|komad|set|panel|pak|paket|par|rola|role|l|lit|metar|kut"
_PRICE_IN_LABEL = re.compile(
    rf"{_NOT_IN_A_NUMBER}(?:{_NUMBER})\s*/?\s*km(?![^\W\d_])"
    rf"(?:\s*/\s*(?:{_PER_UNIT})(?![^\W\d_]))?"
)
# Punctuation and symbols, except a dot, comma or slash inside a number ("1,10", "3/8").
_PUNCTUATION = re.compile(r"(?<!\d)[.,/]|[.,/](?!\d)|[^\w\s.,/]|_")
# A space that doesn't stand between two digits ("153 x 250" = "153X250", but "1,5 10" stays
# two numbers).
_SPACE_NOT_BETWEEN_DIGITS = re.compile(r"(?<!\d) | (?!\d)")
# Where a price was removed from the label (a character no label contains).
_PRICE_MARK = "\x00"
# Filler words that, written after the price and before the end of the label, don't name a
# different variant: "... - 2,55KM/KOM I", "100mm - 2,00KM - NEMA" (Tomislav, 27 Nov 2026).
_FILLER_AFTER_PRICE = re.compile(
    rf"{_PRICE_MARK}(?:[^\w{_PRICE_MARK}]*(?<![^\W\d_])(?:i|nema)(?![^\W\d_]))+[^\w]*$"
)


def _without_accents(text: str) -> str:
    """ "Kočnicom" -> "kocnicom": drop the marks NFKD splits off (č, ć, š, ž; đ stays đ)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _remove_bare_price(label: str, price_text: str | None) -> str:
    """Remove the variant's price where the label writes it without "KM" ("... - 12").

    Only at the price position: right after a separator (hyphen, en dash or colon) and followed by
    nothing but the end of the label or punctuation, so "Lanac 6mm" or "Cijev fi 20" keep
    their number even when the price is 6 or 20. The last such place counts.
    """
    number = _NUMBER_AND_UNIT.search(price_text.casefold()) if price_text else None
    if number is None:
        return label
    pattern = rf"([-\u2013:]\s*){re.escape(number.group(1))}(?![\d.,]*\d)(?=\s*(?:$|[^\w\s]))"
    matches = list(re.finditer(pattern, label))
    if not matches:
        return label
    last = matches[-1]
    return f"{label[: last.start()]}{last.group(1)}{_PRICE_MARK}{label[last.end() :]}"


def _label_without_price(v: VariantRow) -> str:
    """The label with its prices removed, compared ignoring case, accents, spaces and
    punctuation: "60mm - 1,50KM" and "60MM - 1,25KM" are both "60mm".

    Removes every "<number> KM" and the variant's own price where the label writes it
    without "KM" (`_remove_bare_price`), then filler written after the last price ("I",
    "NEMA"). Spaces go, except between two numbers.
    """
    label = _PRICE_IN_LABEL.sub(_PRICE_MARK, v.label.casefold())
    label = _remove_bare_price(label, v.price_text)
    label = _FILLER_AFTER_PRICE.sub("", label).replace(_PRICE_MARK, " ")
    label = " ".join(_PUNCTUATION.sub(" ", _without_accents(label)).split())
    return _SPACE_NOT_BETWEEN_DIGITS.sub("", label)


def check_price_conflicts(listing: CheckListing) -> list[ReviewIssue]:
    """Variants whose labels are identical once the price is removed, in the same unit and
    with the same sizes and colour, at different prices (Tomislav's rule, 27 Nov 2026).

    Labels that differ in anything else (INOX vs CINK, "s kočnicom", wire thickness, DUGI
    vs KRATKI, the brand) are different products, not a conflict. Prices in different units
    (a roll and a metre, a panel and a piece) are not compared.
    """
    groups: dict[tuple[str, str | None, str], list[VariantRow]] = defaultdict(list)
    for v in _priced(listing):
        groups[(_label_without_price(v), _size_key(v), _normal_text(v.unit))].append(v)
    conflicts = [
        (group, variants)
        for group, variants in groups.items()
        # A label that is only a price (empty once it's removed) and no sizes: nothing
        # says the variants are the same product.
        if len({v.price for v in variants}) > 1 and (group[0] or group[1] is not None)
    ]
    # The fingerprint key is the sizes the listing wrote and the unit, as before this rule
    # (so Toni's ignored rows keep their fingerprint). The label joins it when the variants
    # have no sizes, and for the second and later conflicts that share the same sizes (the
    # first, in listing order, keeps the plain key).
    seen_sizes: set[tuple[str, str]] = set()
    issues = []
    for (label, sizes, unit), variants in conflicts:
        if sizes is None:
            key = f"label:{label}|{unit}"
        elif (sizes, unit) in seen_sizes:
            key = f"{sizes}|{unit}|label:{label}"
        else:
            key = f"{sizes}|{unit}"
            seen_sizes.add((sizes, unit))
        what = "isti artikal je naveden" if sizes is None else "ista mjera je navedena"
        prices = sorted({v.price for v in variants if v.price is not None})
        labels = [v.label for v in variants]
        issues.append(
            ReviewIssue(
                "price_conflict",
                listing.id,
                "high",
                f"Oglas {listing.id}: {what} s različitim cijenama: "
                f"{' i '.join(format_km(p) for p in prices)} ({'; '.join(labels)}).",
                {"labels": labels, "prices": [str(p) for p in prices]},
                key=key,
            )
        )
    return issues


def check_listing_price(listing: CheckListing) -> list[ReviewIssue]:
    """The price OLX shows for the listing is none of the prices in its description."""
    prices = {v.price for v in _priced(listing)}
    if not listing.price or not prices or listing.price in prices:
        return []
    return [
        ReviewIssue(
            "listing_price_mismatch",
            listing.id,
            "medium",
            f"Oglas {listing.id}: cijena oglasa {format_km(listing.price)} ne odgovara nijednoj "
            "cijeni u opisu.",
            {"listing_price": str(listing.price), "variant_prices": sorted(map(str, prices))},
            key=str(listing.price),
        )
    ]


# --- material, state, photos -------------------------------------------------------------------

# OLX "Materijal ograde" values and words the title, product type or material must contain
# for the value to fit. Values not listed here ("Ostalo", "PVC", ...) aren't checked.
_MATERIAL_WORDS: Mapping[str, tuple[str, ...]] = {
    "kovana": ("kovan",),
    "panelna": ("panel",),
}


def check_material(listing: CheckListing) -> list[ReviewIssue]:
    """An OLX material attribute the listing contradicts, e.g. "Kovana" on wire mesh.

    The model sees the OLX fields too, so the extracted `material` may just repeat the
    attribute ("Kovana"). That copy is removed before matching: the attribute can't be
    its own evidence.
    """
    issues = []
    for name, value in listing.attributes:
        words = _MATERIAL_WORDS.get(value.casefold())
        if name.casefold() != "materijal ograde" or not words:
            continue
        parts = [listing.title]
        if listing.product:
            material = (listing.product.material or "").casefold().replace(value.casefold(), "")
            parts += [listing.product.product_type, material]
        text = " ".join(parts).casefold()
        if not any(w in text for w in words):
            issues.append(
                ReviewIssue(
                    "wrong_material",
                    listing.id,
                    "medium",
                    f"Oglas {listing.id}: atribut „{name}“ je „{value}“, a naslov i opis govore "
                    "o drugom materijalu. Provjerite atribut.",
                    {"attribute": name, "value": value},
                    key=value,
                )
            )
    return issues


def check_used_state(listing: CheckListing) -> list[ReviewIssue]:
    """A listing marked "Korišteno" (used); Martom sells new goods."""
    if listing.state != "used":
        return []
    return [
        ReviewIssue(
            "used_state",
            listing.id,
            "medium",
            f"Oglas {listing.id}: oglas je označen kao „Korišteno“. Ako je roba nova, "
            "promijenite stanje u „Novo“.",
            {"state": listing.state},
        )
    ]


def check_missing_photos(listing: CheckListing) -> list[ReviewIssue]:
    if listing.photo_count > 0:
        return []
    return [
        ReviewIssue(
            "missing_photos",
            listing.id,
            "medium",
            f"Oglas {listing.id}: oglas nema nijednu fotografiju.",
        )
    ]


# --- typos and markup ---------------------------------------------------------------------------

# Misspellings seen in real Martom listings, with the right word. Grow this list as Toni's
# report turns up more; a word only goes in when it can't be a correct word in another form.
KNOWN_TYPOS: Mapping[str, str] = {
    "pluči": "ploči",
}
_WORD = re.compile(r"[^\W\d_]+")
# The same word twice in a row ("za za"); letters only, so sizes like "83 83" don't count.
# Only spaces or tabs between the two, so a line break ("za ogradu\nOgradu nudimo") is not
# a doubled word; the title and the description are checked separately for the same reason.
_DOUBLED = re.compile(r"\b([^\W\d_]+)[ \t]+\1\b", re.IGNORECASE)


def check_typos(listing: CheckListing) -> list[ReviewIssue]:
    found: dict[str, str | None] = {}
    for text in (listing.title, listing.description_text):
        for match in _DOUBLED.finditer(text):
            found.setdefault(" ".join(match.group(0).casefold().split()), None)
    for word in _WORD.findall(f"{listing.title}\n{listing.description_text}".casefold()):
        if word in KNOWN_TYPOS:
            found.setdefault(word, KNOWN_TYPOS[word])
    issues = []
    for word, correction in found.items():
        hint = f" (treba „{correction}“)" if correction else " (riječ je ponovljena)"
        issues.append(
            ReviewIssue(
                "typo",
                listing.id,
                "low",
                f"Oglas {listing.id}: moguća greška u tekstu: „{word}“{hint}.",
                {"word": word, "correction": correction},
                key=word,
            )
        )
    return issues


# LaTeX commands (\varnothing, \text{...}), "$...\" math, HTML entities and tags left in the
# cleaned description text.
_MARKUP = re.compile(
    r"\\[a-zA-Z]{2,}|\$[^$\n]*\\|&(?:[a-zA-Z]+|#\d+);|</?[a-zA-Z][a-zA-Z0-9]*\s*/?>"
)


def check_markup(listing: CheckListing) -> list[ReviewIssue]:
    match = _MARKUP.search(listing.description_text)
    if match is None:
        return []
    start = max(match.start() - 20, 0)
    sample = " ".join(listing.description_text[start : match.end() + 20].split())
    return [
        ReviewIssue(
            "markup",
            listing.id,
            "low",
            f"Oglas {listing.id}: opis sadrži ostatke formatiranja (LaTeX ili HTML), npr. "
            f"„{sample}“.",
            {"sample": sample},
        )
    ]


# --- whole catalogue -----------------------------------------------------------------------------


def _title_key(title: str) -> str:
    return " ".join(_WORD_OR_NUMBER.findall(title.casefold()))


_WORD_OR_NUMBER = re.compile(r"[^\W_]+")


def check_duplicate_listings(listings: Iterable[CheckListing]) -> list[ReviewIssue]:
    """Two or more active listings with the same title (ignoring case and punctuation)."""
    groups: dict[str, list[CheckListing]] = defaultdict(list)
    for listing in listings:
        key = _title_key(listing.title)
        if key:
            groups[key].append(listing)
    issues = []
    for title_key, group in groups.items():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda listing: listing.id)
        ids = [listing.id for listing in group]
        prices = sorted({listing.price for listing in group if listing.price})
        ids_text = ", ".join(map(str, ids))
        if len(prices) > 1:
            priority: Priority = "high"
            what = f"po različitim cijenama ({' i '.join(format_km(p) for p in prices)})"
        else:
            priority = "medium"
            what = "po istoj cijeni"
        issues.append(
            ReviewIssue(
                "duplicate_listing",
                ids[0],
                priority,
                f"Isti proizvod je u {len(ids)} oglasa ({ids_text}) {what}. Ostavite jedan oglas "
                "ili uskladite cijene.",
                {"listing_ids": ids, "prices": [str(p) for p in prices]},
                # The shared title, not the ids: a third copy or a removed one is still the
                # same problem, and keeps Toni's ignored/open status.
                key=title_key,
                per_listing=False,
            )
        )
    return issues


def check_manual_products(
    products: Mapping[str, ManualProduct], *, today: date
) -> list[ReviewIssue]:
    """manual_products.toml entries last confirmed more than 90 days ago."""
    return [
        ReviewIssue(
            "manual_product_stale",
            None,
            "high",
            f"Cijena proizvoda bez OLX oglasa „{p.name}“ ({format_km(p.price_km)}) zadnji put je "
            f"potvrđena {p.last_confirmed:%d.%m.%Y}., prije više od {MAX_AGE_DAYS} dana. "
            "Potvrdite ili ispravite cijenu.",
            {"product": p.key, "last_confirmed": p.last_confirmed.isoformat()},
            # A new confirmation date is a new question for Toni, so it gets a new issue.
            key=f"{p.key}:{p.last_confirmed.isoformat()}",
        )
        for p in stale_manual_products(dict(products), today=today)
    ]


# --- allowlisted listings (#45) ------------------------------------------------------------------

# The sync's reasons that mean OLX no longer shows the listing (src/martom/olx/allowlist.py).
_GONE_REASONS = {
    "404": "OLX javlja da oglas ne postoji (404)",
    "inactive": "oglas više nije aktivan",
    "not_martom": "oglas više nije na Martomovom profilu",
}


def check_allowlist_warnings(warnings: Iterable[Mapping[str, Any]]) -> list[ReviewIssue]:
    """Issues from the latest sync's allowlist warnings (`sync_run.allowlist_warnings`).

    A fence listing outside "Ograde" that OLX no longer shows is high: the BOM needs it.
    One that is still active but that the sync found in none of the categories it pages
    ("not_seen": it moved out of all of them) is medium; the caller passes "not_seen" only
    from a run with 100% coverage (`review.store.load_allowlist_warnings`).
    "recheck_failed" (a failed GET, or a row without a category_id) is a sync-side glitch
    for Tomislav and raises nothing here, and so does "moved", which runs before R1-3
    could record. `name` comes from our
    own allowlist file.
    """
    issues: list[ReviewIssue] = []
    for warning in warnings:
        if not isinstance(warning, Mapping):
            continue
        listing_id, reason = warning.get("listing_id"), warning.get("reason")
        if not isinstance(listing_id, int) or isinstance(listing_id, bool):
            continue
        name = str(warning.get("name") or "")
        expected, category = warning.get("expected_category_id"), warning.get("category_id")
        details = {
            "allowlisted_listing_id": listing_id,
            "reason": reason,
            "expected_category_id": expected,
            "category_id": category,
        }
        if reason in _GONE_REASONS:
            issues.append(
                ReviewIssue(
                    "allowlist_listing_gone",
                    # Not the listing's row: it may never have been stored (the FK), and an
                    # inactive listing would hide the issue from the report.
                    None,
                    "high",
                    f"Oglas {listing_id} ({name}) treba za ogradu, ali {_GONE_REASONS[reason]}. "
                    "Provjerite oglas na OLX-u; ako je namjerno uklonjen, javite Tomislavu da "
                    "ga makne s liste oglasa koje sinkronizacija posebno prati.",
                    details,
                    # Not the reason: 404 today and "inactive" tomorrow is the same problem.
                    key=f"{listing_id}:gone",
                )
            )
        elif reason == "not_seen":
            issues.append(
                ReviewIssue(
                    "allowlist_listing_not_synced",
                    None,  # as above: the listing may not be stored
                    "medium",
                    f"Oglas {listing_id} ({name}) treba za ogradu i aktivan je na OLX-u, ali ga "
                    "sinkronizacija nije našla ni u jednoj kategoriji. Provjerite u kojoj je "
                    "kategoriji oglas i javite Tomislavu.",
                    details,
                    key=f"{listing_id}:not_synced",
                )
            )
    return issues


# --- BOM price map (#125) -------------------------------------------------------------------------

_PRICE_PROBLEM_TEXT = {
    PriceProblem.LISTING_INACTIVE: "oglas {listing} nije aktivan ili još nije obrađen",
    PriceProblem.VARIANT_MISSING: "u oglasu {listing} više nema varijante {fields}",
    PriceProblem.VARIANT_AMBIGUOUS: "u oglasu {listing} ima više varijanti {fields}",
    PriceProblem.UNPRICED: "varijanta {fields} u oglasu {listing} nema cijenu",
    PriceProblem.PRICES_DIFFER: "varijante {fields} u oglasu {listing} više nemaju istu cijenu",
    PriceProblem.MANUAL_MISSING: "proizvod „{manual}“ nije u manual_products.toml",
}
# A manual entry has no listing and no variants: its own text (R2-2).
_MANUAL_UNPRICED_TEXT = "proizvod „{manual}“ u manual_products.toml nema cijenu (0 KM)"


def check_bom_price_map(
    price_map: PriceMap,
    listings: Iterable[CheckListing],
    manual_products: Mapping[str, ManualProduct],
) -> list[ReviewIssue]:
    """A BOM line the price map prices that has lost its price (high: the customer sees
    "cijena na upit" in the materials list).

    It also runs while the map is a draft, as a check of the map before Tomislav and Toni
    review it; then every line is "cijena na upit" because of the draft, so the message
    says that instead of blaming the variant.

    Uses `resolve_price`, the same rule as the DB pricer, so Toni sees exactly the lines
    the customer sees unpriced. `listings` are the active listings; one without an
    extracted product counts as inactive. Lines the map leaves unmapped on purpose raise
    nothing. The issue carries the listing only while it is active (the report hides
    issues of inactive listings); its fingerprint is the BOM item and size alone, so a
    variant going missing and then its listing going inactive stays one issue.
    """
    active = {listing.id: listing.variants for listing in listings if listing.product}
    issues: list[ReviewIssue] = []
    for entry in price_map.entries.values():
        variants = active.get(entry.listing_id) if entry.listing_id is not None else None
        lookup = resolve_price(entry, variants, manual_products)
        problem = lookup.problem
        if problem is None:
            continue
        line = f"{entry.item.value} {entry.size}" if entry.size else entry.item.value
        # Only the variant that failed (one tensioner colour), or all for an inactive
        # listing or colours whose prices differ.
        match_dicts = [dict(match) for match in lookup.failed_matches or entry.matches]
        fields = ", ".join(_match_text(m) for m in match_dicts)
        template = (
            _MANUAL_UNPRICED_TEXT
            if entry.manual_key is not None and problem == PriceProblem.UNPRICED
            else _PRICE_PROBLEM_TEXT[problem]
        )
        reason = template.format(listing=entry.listing_id, fields=fields, manual=entry.manual_key)
        if price_map.status == "reviewed":
            sees = "(kupac vidi „cijena na upit“)"
        else:
            sees = "(mapa cijena je još nacrt, pa kupac ionako vidi „cijena na upit“ za sve stavke)"
        if entry.manual_key is not None:
            action = "Javite Tomislavu ispravnu cijenu za manual_products.toml."
        else:
            action = (
                "Provjerite oglas; ako se oglas namjerno promijenio, javite Tomislavu da "
                "uskladi mapu cijena (price_map.toml)."
            )
        issues.append(
            ReviewIssue(
                "bom_price_missing",
                entry.listing_id if entry.listing_id in active else None,
                "high",
                f"Stavka „{line}“ u popisu materijala za ogradu nema cijenu {sees}: "
                f"{reason}. {action}",
                {
                    "item": entry.item.value,
                    "size": entry.size,
                    "listing_id": entry.listing_id,
                    "problem": problem.value,
                    "variants": match_dicts,
                },
                key=f"{entry.item.value}:{entry.size or ''}",
                per_listing=False,
            )
        )
    return issues


def _match_text(match: Mapping[str, str | int]) -> str:
    """A map variant table as Toni reads it: „153 cm / 250 cm / panel“."""
    parts = [
        f"{value} cm" if field.endswith("_cm") else str(value) for field, value in match.items()
    ]
    return "„" + " / ".join(parts) + "“"


# --- all checks ----------------------------------------------------------------------------------

LISTING_CHECKS: tuple[Callable[[CheckListing], list[ReviewIssue]], ...] = (
    check_implausible_dimensions,
    check_variant_prices,
    check_price_on_request,
    check_price_conflicts,
    check_listing_price,
    check_material,
    check_used_state,
    check_missing_photos,
    check_typos,
    check_markup,
)


def check_listing(listing: CheckListing) -> list[ReviewIssue]:
    """Every per-listing check on one listing."""
    return [issue for check in LISTING_CHECKS for issue in check(listing)]


def run_all_checks(
    listings: Sequence[CheckListing],
    manual_products: Mapping[str, ManualProduct],
    *,
    today: date,
    allowlist_warnings: Iterable[Mapping[str, Any]] = (),
    price_map: PriceMap | None = None,
) -> list[ReviewIssue]:
    """Every check over the whole catalogue, the manual-product dates, the latest
    sync's allowlist warnings and (when given) the BOM price map."""
    issues = [issue for listing in listings for issue in check_listing(listing)]
    issues += check_duplicate_listings(listings)
    issues += check_manual_products(manual_products, today=today)
    issues += check_allowlist_warnings(allowlist_warnings)
    if price_map is not None:
        issues += check_bom_price_map(price_map, listings, manual_products)
    return issues
