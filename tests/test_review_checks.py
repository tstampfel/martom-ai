"""Unit tests for the deterministic review checks (martom.review.checks).

Each check is a plain function over one listing (or the whole catalogue) that returns
ReviewIssue values; nothing here touches the database or changes extracted data.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from martom.bom.manual_products import ManualProduct
from martom.extract.store import VariantRow
from martom.review.checks import (
    CheckListing,
    CheckProduct,
    ReviewIssue,
    check_duplicate_listings,
    check_implausible_dimensions,
    check_listing,
    check_listing_price,
    check_manual_products,
    check_markup,
    check_material,
    check_missing_photos,
    check_price_conflicts,
    check_price_on_request,
    check_typos,
    check_used_state,
    check_variant_prices,
)


def variant(
    label: str = "Panel",
    *,
    height: str | None = None,
    length: str | None = None,
    colour: str | None = None,
    size: str | None = None,
    price_text: str | None = "10,00 KM",
    price: Decimal | None = Decimal("10.00"),
    unit: str | None = None,
) -> VariantRow:
    return VariantRow(
        label=label,
        height=height,
        length=length,
        colour=colour,
        size=size,
        price_text=price_text,
        price=price,
        unit=unit,
    )


def listing(
    *,
    variants: list[VariantRow] | None = None,
    product_type: str = "panelna ograda",
    **overrides: object,
) -> CheckListing:
    base = CheckListing(
        id=1,
        slug="panel-1",
        title="PANELNA OGRADA",
        description_text="Panelna ograda.",
        price=Decimal("10.00"),
        display_price="10 KM",
        price_by_agreement=False,
        state="new",
        attributes=(),
        photo_count=1,
        product=CheckProduct(product_type=product_type, material=None, variants=variants or []),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def kinds(issues: list[ReviewIssue]) -> list[str]:
    return [issue.kind for issue in issues]


# --- ReviewIssue -------------------------------------------------------------------------


def test_fingerprint_is_stable_and_ignores_message_and_priority() -> None:
    a = ReviewIssue("used_state", 1, "medium", "Poruka", {}, key="state")
    b = ReviewIssue("used_state", 1, "high", "Druga poruka", {"x": 1}, key="state")
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint) == 64


def test_fingerprint_differs_by_kind_listing_and_key() -> None:
    base = ReviewIssue("used_state", 1, "medium", "m", {}, key="a")
    assert base.fingerprint != replace(base, kind="missing_photos").fingerprint
    assert base.fingerprint != replace(base, listing_id=2).fingerprint
    assert base.fingerprint != replace(base, key="b").fingerprint
    assert base.fingerprint != replace(base, listing_id=None).fingerprint


# --- implausible_dimension ---------------------------------------------------------------


def test_panel_length_in_wrong_unit_is_implausible() -> None:
    issues = check_implausible_dimensions(
        listing(variants=[variant("PANEL 153 X 2500CM", height="153", length="2500CM")])
    )
    assert kinds(issues) == ["implausible_dimension"]
    assert issues[0].priority == "high"
    assert "2500CM" in issues[0].message
    assert issues[0].details["field"] == "length"


def test_normal_panel_sizes_pass() -> None:
    issues = check_implausible_dimensions(
        listing(
            variants=[
                variant(height="83", length="200 cm"),
                variant(height="203", length="250 cm"),
                variant(height="1530 mm", length="2500 mm"),
            ]
        )
    )
    assert issues == []


def test_panel_height_out_of_range_is_implausible() -> None:
    issues = check_implausible_dimensions(listing(variants=[variant(height="1530", length="250")]))
    assert kinds(issues) == ["implausible_dimension"]
    assert issues[0].details["field"] == "height"


def test_rolls_in_metres_are_plausible_for_any_product() -> None:
    issues = check_implausible_dimensions(
        listing(
            product_type="bodljikava žica",
            variants=[
                variant(length="250 M"),
                variant(length="ROLA 50M"),
                variant(length="20METARA"),
            ],
        )
    )
    assert issues == []


def test_huge_centimetre_value_is_implausible_for_any_product() -> None:
    issues = check_implausible_dimensions(
        listing(product_type="mreža za kokoši", variants=[variant(height="100CM", length="2500CM")])
    )
    assert kinds(issues) == ["implausible_dimension"]


def test_post_heights_are_checked() -> None:
    ok = listing(product_type="stup za ogradu", variants=[variant(height="H-155 cm")])
    bad = listing(product_type="stup za ogradu", variants=[variant(height="H-1550 cm")])
    assert check_implausible_dimensions(ok) == []
    assert kinds(check_implausible_dimensions(bad)) == ["implausible_dimension"]


def test_unit_written_once_in_the_label_applies_to_both_sizes() -> None:
    # 1199211: "2500 x 1030 mm" is extracted as length "2500", height "1030 mm".
    issues = check_implausible_dimensions(
        listing(variants=[variant("2500 x 1030 mm", height="1030 mm", length="2500")])
    )
    assert issues == []


def test_unreadable_sizes_are_ignored() -> None:
    issues = check_implausible_dimensions(listing(variants=[variant(height="visoka", length=None)]))
    assert issues == []


# --- variant prices -----------------------------------------------------------------------


def test_height_without_price() -> None:
    issues = check_variant_prices(
        listing(
            variants=[
                variant("Panel 153 x 250 cm", height="153"),
                variant("Panel 173 x 250 cm", height="173", price_text=None, price=None),
            ]
        )
    )
    assert kinds(issues) == ["height_without_price"]
    assert issues[0].priority == "high"
    assert "173" in issues[0].message


def test_variant_without_height_or_price() -> None:
    issues = check_variant_prices(
        listing(variants=[variant("Kopča", price_text=None, price=None), variant("Anker")])
    )
    assert kinds(issues) == ["variant_without_price"]


def test_unreadable_price() -> None:
    issues = check_variant_prices(
        listing(variants=[variant(price_text="27,00 / 31,00 KM", price=None)])
    )
    assert kinds(issues) == ["unreadable_price"]
    assert "27,00 / 31,00 KM" in issues[0].message


def test_listing_without_any_variant_price_is_left_to_price_on_request() -> None:
    # 1199211 ("Na upit") lists 19 sizes and no price at all: one price_on_request issue
    # covers it, not 19 height_without_price issues.
    unpriced = [variant(height=h, price_text=None, price=None) for h in ("83", "103")]
    assert check_variant_prices(listing(variants=unpriced)) == []


def test_priced_variants_pass() -> None:
    assert check_variant_prices(listing(variants=[variant(height="153")])) == []


def test_listing_without_product_has_no_variant_issues() -> None:
    assert check_variant_prices(listing(product=None)) == []
    assert check_implausible_dimensions(listing(product=None)) == []


# --- price_on_request ----------------------------------------------------------------------


def test_na_upit_without_any_price_is_flagged() -> None:
    issue_listing = listing(
        price=Decimal("0"),
        display_price="Na upit",
        price_by_agreement=True,
        variants=[variant(price_text=None, price=None)],
    )
    assert kinds(check_price_on_request(issue_listing)) == ["price_on_request"]


def test_na_upit_with_prices_in_the_description_passes() -> None:
    priced = listing(
        price=Decimal("0"), display_price="Na upit", price_by_agreement=True, variants=[variant()]
    )
    assert check_price_on_request(priced) == []


def test_unknown_price_is_not_on_request() -> None:
    # price NULL: the detail wasn't fetched yet, so nothing is known about the price.
    assert check_price_on_request(listing(price=None, display_price=None, variants=[])) == []


def test_priced_listing_is_not_on_request() -> None:
    assert check_price_on_request(listing()) == []


# --- price conflicts -----------------------------------------------------------------------


def test_same_variant_at_two_prices() -> None:
    issues = check_price_conflicts(
        listing(
            variants=[
                variant("Panel 153 x 250", height="153", length="250 cm"),
                variant(
                    "PANEL 153X250",
                    height="153",
                    length="250 CM",
                    price_text="12,00 KM",
                    price=Decimal("12.00"),
                ),
            ]
        )
    )
    assert kinds(issues) == ["price_conflict"]
    assert "10,00" in issues[0].message and "12,00" in issues[0].message


def test_same_variant_at_the_same_price_is_not_a_conflict() -> None:
    issues = check_price_conflicts(
        listing(variants=[variant(height="153", length="250"), variant(height="153", length="250")])
    )
    assert issues == []


def test_other_sizes_are_compared_as_text() -> None:
    # 24449780: "100/8/15" and "100/8/30" are different meshes, not both "100".
    issues = check_price_conflicts(
        listing(
            variants=[
                variant(height="100CM", size="100/8/15", price=Decimal("88")),
                variant(height="100CM", size="100/8/30", price=Decimal("63")),
            ]
        )
    )
    assert issues == []


def test_listing_price_compares_cents() -> None:
    assert check_listing_price(listing(price=Decimal("10.0"), variants=[variant()])) == []


def test_variants_without_any_size_are_not_compared() -> None:
    issues = check_price_conflicts(
        listing(variants=[variant("Kopča"), variant("Anker", price=Decimal("1.30"))])
    )
    assert issues == []


def test_listing_price_not_among_variant_prices() -> None:
    issues = check_listing_price(listing(price=Decimal("30.00"), variants=[variant()]))
    assert kinds(issues) == ["listing_price_mismatch"]


def test_listing_price_among_variant_prices_passes() -> None:
    assert check_listing_price(listing(price=Decimal("10.00"), variants=[variant()])) == []
    assert check_listing_price(listing(price=Decimal("0"), variants=[variant()])) == []
    assert check_listing_price(listing(price=Decimal("30"), variants=[])) == []


# --- material, used, photos -----------------------------------------------------------------


def test_wrought_attribute_on_wire_mesh() -> None:
    issues = check_material(
        listing(
            title="FARMER PLETIVO OGRADA",
            product_type="pletivo",
            attributes=(("Materijal ograde", "Kovana"),),
        )
    )
    assert kinds(issues) == ["wrong_material"]
    assert "Kovana" in issues[0].message


def test_material_attribute_that_matches_passes() -> None:
    panel = listing(attributes=(("Materijal ograde", "Panelna"),))
    other = listing(attributes=(("Materijal ograde", "Ostalo"),))
    wrought = listing(
        title="KOVANA OGRADA",
        product_type="kovana ograda",
        attributes=(("Materijal ograde", "Kovana"),),
    )
    assert check_material(panel) == []
    assert check_material(other) == []
    assert check_material(wrought) == []


def test_used_state_is_flagged() -> None:
    assert kinds(check_used_state(listing(state="used"))) == ["used_state"]
    assert check_used_state(listing(state="new")) == []
    assert check_used_state(listing(state=None)) == []


def test_missing_photos() -> None:
    assert kinds(check_missing_photos(listing(photo_count=0))) == ["missing_photos"]
    assert check_missing_photos(listing(photo_count=3)) == []


# --- typos and markup -------------------------------------------------------------------------


def test_known_typo_and_doubled_word() -> None:
    issues = check_typos(
        listing(title="OGRADA ZA ZA DVORIŠTE", description_text="Stupovi sa 4 rupe na pluči.")
    )
    assert kinds(issues) == ["typo", "typo"]
    assert {issue.details["word"] for issue in issues} == {"za za", "pluči"}


def test_clean_text_has_no_typos() -> None:
    assert check_typos(listing(description_text="Stupovi su od profila 50x50 mm.")) == []
    # Numbers repeated on purpose ("83 83") are sizes, not doubled words.
    assert check_typos(listing(title="PANEL 83 83 CM")) == []


def test_latex_and_html_markup() -> None:
    latex = listing(description_text=r"debljine $\varnothing 1,80\text{ mm}$")
    html = listing(description_text="Cijena&nbsp;po komadu <br> 5 KM")
    assert kinds(check_markup(latex)) == ["markup"]
    assert kinds(check_markup(html)) == ["markup"]
    assert check_markup(listing(description_text="Cijena 5 KM (Ø 4,20 mm).")) == []


# --- duplicates (catalogue-wide) -------------------------------------------------------------


def test_same_title_at_two_prices_is_one_issue() -> None:
    a = listing(id=5, title="PANELNA OGRADA 153 CM", price=Decimal("44"))
    b = listing(id=3, title="Panelna  ograda 153 cm!", price=Decimal("53"))
    c = listing(id=9, title="BRUSILICA", price=Decimal("89"))
    issues = check_duplicate_listings([a, b, c])
    assert kinds(issues) == ["duplicate_listing"]
    issue = issues[0]
    assert issue.listing_id == 3
    assert issue.details["listing_ids"] == [3, 5]
    assert issue.priority == "high"


def test_same_title_same_price_is_a_lower_priority_duplicate() -> None:
    a = listing(id=5, title="PANEL", price=Decimal("44"))
    b = listing(id=3, title="PANEL", price=Decimal("44"))
    issues = check_duplicate_listings([a, b])
    assert kinds(issues) == ["duplicate_listing"]
    assert issues[0].priority == "medium"


def test_duplicate_fingerprint_does_not_depend_on_order() -> None:
    a = listing(id=5, title="PANEL")
    b = listing(id=3, title="PANEL")
    assert (
        check_duplicate_listings([a, b])[0].fingerprint
        == check_duplicate_listings([b, a])[0].fingerprint
    )


# --- manual products ------------------------------------------------------------------------


def manual(last_confirmed: date) -> ManualProduct:
    return ManualProduct(
        key="anchor_m8x70",
        name="Anker M8x70",
        price_km=Decimal("0.80"),
        source="Toni 26.11.2026",
        last_confirmed=last_confirmed,
    )


def test_manual_product_older_than_90_days() -> None:
    issues = check_manual_products(
        {"anchor_m8x70": manual(date(2026, 1, 1))}, today=date(2026, 4, 2)
    )
    assert kinds(issues) == ["manual_product_stale"]
    assert issues[0].listing_id is None
    assert "Anker M8x70" in issues[0].message
    assert "0,80 KM" in issues[0].message


def test_manual_product_at_exactly_90_days_is_fine() -> None:
    products = {"anchor_m8x70": manual(date(2026, 1, 1))}
    assert check_manual_products(products, today=date(2026, 4, 1)) == []


def test_reconfirmed_manual_product_gets_a_new_fingerprint() -> None:
    old = check_manual_products({"a": manual(date(2026, 1, 1))}, today=date(2026, 6, 1))
    new = check_manual_products({"a": manual(date(2026, 2, 1))}, today=date(2026, 6, 1))
    assert old[0].fingerprint != new[0].fingerprint


# --- check_listing runs every per-listing check -------------------------------------------------


def test_check_listing_collects_every_check() -> None:
    issues = check_listing(
        listing(
            state="used",
            photo_count=0,
            variants=[
                variant(height="153", length="250 cm"),
                variant(height="173", length="2500CM", price_text=None, price=None),
            ],
        )
    )
    assert sorted(kinds(issues)) == [
        "height_without_price",
        "implausible_dimension",
        "missing_photos",
        "used_state",
    ]


def test_messages_are_bosnian_and_name_the_listing() -> None:
    issue = check_used_state(listing(state="used"))[0]
    assert "Korišteno" in issue.message
    assert issue.message.startswith("Oglas 1")


# --- review round 1 ----------------------------------------------------------------------------

# R1-1: a unit counts only when it sits right next to the number (optional space) and is not
# the start of a longer word; a bare number takes its unit only from the label's same number.
SIZE_CASES = [
    ("153", "Panel 153 maslinasto zelena", Decimal(153)),  # "m" of a colour word
    ("153 mat", "", Decimal(153)),
    ("153 metalni", "", Decimal(153)),
    ("5", "Rola 25 m, visina 5", Decimal(5)),  # "5" is not the "5" in "25"
    ("153", "Panel 153 + stup 60x40mm", Decimal(153)),  # mm belongs to another number
    ("1.530 mm", "", Decimal(153)),  # thousands dot, like parse_price_km
    ("1.250,5 cm", "", Decimal("1250.5")),
    ("4,20 m", "", Decimal(420)),
    ("4.20 m", "", Decimal(420)),
    ("2500", "2500 x 1030 mm", Decimal(250)),  # "A x B unit"
    ("153", "PANEL 153 X 2500CM", Decimal(153)),
    ("83", "3D Panelna ograda 83 x 200 cm (Ø 4,20/4,40 mm PVC)", Decimal(83)),
    ("ROLA 50M", "", Decimal(5000)),
    ("20METARA", "", Decimal(2000)),
    ("1530MM", "", Decimal(153)),
    ("H-85 cm", "", Decimal(85)),
    ("2500CM", "", Decimal(2500)),
    # Round 2: a number after an abbreviation (letter + dot, or letter + space) is read.
    ("H.2500CM", "", Decimal(2500)),
    ("v.150 cm", "", Decimal(150)),
    ("H.100 cm", "", Decimal(100)),
    ("H 100 cm", "", Decimal(100)),
    ("dim.153x250", "", Decimal(153)),
    # ... but a word starting with "m" right after the number is still not metres.
    ("H.153 maslinasto", "", Decimal(153)),
    # Round 2 reviewer probes that must keep working.
    ("830 mm", "2500x 830 mm", Decimal(83)),
    ("2500", "2500x 830 mm", Decimal(250)),
    ("100", "100CM (100/8/15)", Decimal(100)),
    ("H-100CM", "", Decimal(100)),
    ("1,53 m", "", Decimal(153)),
    ("1.53m", "", Decimal(153)),
    ("2 metra", "", Decimal(200)),
    ("25", "okce 25MM", Decimal("2.5")),
    ("1.5", "50 x 50 x 1.5 mm", Decimal("0.15")),
    ("103", "83 i 103 cm", Decimal(103)),
    ("10", "RABITZ 100 cm 10m", Decimal(1000)),
    ("h=150cm", "", Decimal(150)),
    ("150cm/20m", "", Decimal(150)),
    ("1530", "1530x2500mm", Decimal(153)),
    ("2500", "2500 \u00d7 1030 mm", Decimal(250)),  # multiplication sign
    # Round 2 notes: the "A x B unit" pair hands on mm/cm only, not metres ("2,5m" is
    # a length in metres, not the unit of the 150 cm height) ...
    ("150", "Panel 150 x 2,5m", Decimal(150)),
    # ... the same number twice in the label with different units is not guessed ...
    ("100", "Panel visina 100, okca 100 x 50 mm", Decimal(100)),
    # ... and with metres a dot is a decimal point ("2.500 m" is 2,5 m, not 2500 m).
    ("2.500 m", "", Decimal(250)),
    ("1.530 mm", "", Decimal(153)),
]


def test_size_parser_reads_only_adjacent_units() -> None:
    from martom.review.checks import size_in_cm

    for text, label, expected in SIZE_CASES:
        assert size_in_cm(text, label) == expected, (text, label)
    assert size_in_cm("visoka") is None
    assert size_in_cm(None) is None


def test_colour_word_after_the_height_is_not_metres() -> None:
    issues = check_implausible_dimensions(
        listing(variants=[variant("Panel 153 maslinasto zelena", height="153", length="250")])
    )
    assert issues == []


def test_panel_gates_are_not_held_to_fence_panel_ranges() -> None:
    gate = listing(
        product_type="panelna kapija",
        variants=[variant("Kapija 153 x 400 cm", height="153", length="400 cm")],
    )
    assert check_implausible_dimensions(gate) == []


# R1-2: prices for different units are not a conflict.
def test_roll_and_per_metre_prices_are_not_a_conflict() -> None:
    issues = check_price_conflicts(
        listing(
            product_type="mreža",
            variants=[
                variant(height="150 cm", unit="rola", price=Decimal("100")),
                variant(height="150 cm", unit="m", price=Decimal("4")),
            ],
        )
    )
    assert issues == []


def test_same_unit_written_differently_still_conflicts() -> None:
    issues = check_price_conflicts(
        listing(
            variants=[
                variant(height="153", length="250 cm", unit="Panel", price=Decimal("53")),
                variant(height="153", length="250CM", unit=" panel ", price=Decimal("55")),
            ]
        )
    )
    assert kinds(issues) == ["price_conflict"]


# R1-3: fingerprints are stable when the model rewords a label.
def test_fingerprints_survive_a_reworded_label() -> None:
    def all_fingerprints(label: str) -> set[str]:
        reworded = listing(
            variants=[
                variant(f"{label} A", height="153", length="250 cm"),
                variant(f"{label} B", height="153", length="250 CM", price=Decimal("12")),
                variant(f"{label} C", height="173", length="2500CM", price_text=None, price=None),
                variant(f"{label} D", height="203", price_text="27 / 31 KM", price=None),
            ]
        )
        return {issue.fingerprint for issue in check_listing(reworded)}

    first = all_fingerprints("3D Panelna ograda: 53,00 KM / panel")
    assert len(first) >= 4
    assert first == all_fingerprints("Panel (Ø 4,20 mm PVC)")


def test_duplicate_fingerprint_survives_a_third_listing_and_a_new_lowest_id() -> None:
    a = listing(id=5, title="PANEL 153")
    b = listing(id=3, title="Panel 153")
    c = listing(id=1, title="PANEL  153!")
    pair = check_duplicate_listings([a, b])[0]
    group = check_duplicate_listings([a, b, c])[0]
    assert pair.fingerprint == group.fingerprint
    assert group.details["listing_ids"] == [1, 3, 5]


# R1-4: doubled words only within one line of the title or the description.
def test_doubled_word_across_title_and_description_is_not_a_typo() -> None:
    assert check_typos(listing(title="PANEL ZA OGRADU", description_text="Ogradu nudimo.")) == []
    assert check_typos(listing(description_text="za ogradu\nogradu nudimo")) == []


# --- review round 2 ----------------------------------------------------------------------------


def test_gates_still_get_the_post_ranges() -> None:
    gate_post = listing(product_type="stup za kapiju", variants=[variant(height="600 cm")])
    assert kinds(check_implausible_dimensions(gate_post)) == ["implausible_dimension"]


def test_conflict_and_dimension_checks_read_a_bare_size_the_same_way() -> None:
    # "2500" in "2500 x 1030 mm" is 250 cm in both checks.
    issues = check_price_conflicts(
        listing(
            variants=[
                variant("2500 x 1030 mm", height="1030 mm", length="2500", unit="panel"),
                variant(
                    "Panel 103 x 250 cm",
                    height="103 cm",
                    length="250 cm",
                    unit="panel",
                    price=Decimal("12"),
                ),
            ]
        )
    )
    assert kinds(issues) == ["price_conflict"]


def test_same_size_variants_that_differ_in_the_label_get_their_own_issues() -> None:
    issues = check_variant_prices(
        listing(
            variants=[
                variant("Panel 153x250 Ø4", height="153", length="250", price=Decimal("50")),
                variant(
                    "Panel 153x250 Ø4 PVC", height="153", length="250", price_text=None, price=None
                ),
                variant(
                    "Panel 153x250 Ø5 PVC", height="153", length="250", price_text=None, price=None
                ),
            ]
        )
    )
    assert kinds(issues) == ["height_without_price", "height_without_price"]
    assert len({issue.fingerprint for issue in issues}) == 2


def test_same_size_variants_that_differ_in_the_unit_get_their_own_issues() -> None:
    issues = check_variant_prices(
        listing(
            variants=[
                variant("Mreža 150", height="150", unit="m"),
                variant("Mreža 150 rola", height="150", unit="rola", price_text="?", price=None),
                variant("Mreža 150 kom", height="150", unit="kom", price_text="?", price=None),
            ]
        )
    )
    assert len({issue.fingerprint for issue in issues}) == 2


def test_identical_variants_still_get_their_own_issues() -> None:
    same = variant("Kopča", price_text=None, price=None)
    issues = check_variant_prices(listing(variants=[variant("Anker"), same, same]))
    assert len({issue.fingerprint for issue in issues}) == 2


def test_variant_fingerprint_without_a_collision_ignores_the_label() -> None:
    def fingerprints(label: str) -> set[str]:
        unpriced = variant(f"{label} 173", height="173", length="250", price_text=None, price=None)
        return {
            i.fingerprint for i in check_variant_prices(listing(variants=[variant(), unpriced]))
        }

    assert fingerprints("Panel") == fingerprints("3D panelna ograda")
