from decimal import Decimal

# ruff: noqa: RUF001
from martom.agent.grounding import Facts, check_grounding, facts_from_output, parse_km

BOM_OUTPUT = {
    "type": "bom",
    "lines": [
        {
            "name": "Panel 153 × 250 cm",
            "quantity": 10,
            "unit_price": "53.00",
            "line_total": "530.00",
            "price_on_request": False,
        },
        {
            "name": "Stup 155 cm s pločicom",
            "quantity": 11,
            "unit_price": "24.90",
            "line_total": "273.90",
            "price_on_request": False,
        },
        {
            "name": "Potporni stup 100 cm",
            "quantity": 2,
            "unit_price": None,
            "line_total": None,
            "price_on_request": True,
        },
    ],
    "total": "803.90",
}
PRODUCT_OUTPUT = {"listing_id": 63996799, "title": "Panel", "variants": [{"price": "38.00"}]}


def facts() -> Facts:
    return facts_from_output(BOM_OUTPUT).merge(facts_from_output(PRODUCT_OUTPUT))


def test_facts_collect_prices_ids_quantities_and_unpriced_names() -> None:
    found = facts()
    assert Decimal("53.00") in found.price_set()
    assert Decimal("803.90") in found.price_set()
    assert Decimal("38.00") in found.price_set()
    assert 63996799 in found.listing_ids
    assert {10, 11, 2} <= set(found.quantities)
    assert found.unpriced_names == ["Potporni stup 100 cm"]


def test_parse_km_reads_bosnian_number_formats() -> None:
    assert parse_km("53,00") == Decimal("53.00")
    assert parse_km("1.234,50") == Decimal("1234.50")
    assert parse_km("53") == Decimal("53")
    assert parse_km("24.90") == Decimal("24.90")


def test_grounded_answer_passes() -> None:
    text = (
        "Za 25 m trebate 10 panela po 53,00 KM i 11 stupova (24,90 KM). "
        "Ukupno 803,90 KM. Panel: oglas 63996799."
    )
    assert check_grounding(text, facts()).ok


def test_price_not_from_a_tool_fails() -> None:
    result = check_grounding("Panel košta 49,00 KM.", facts())
    assert not result.ok
    assert "49,00 KM" in result.problems[0]


def test_unknown_listing_id_fails() -> None:
    assert not check_grounding("Pogledajte oglas 12345678.", facts()).ok


def test_quantity_not_from_a_tool_fails() -> None:
    assert not check_grounding("Trebate 12 panela.", facts()).ok


def test_lengths_and_heights_are_not_quantities() -> None:
    assert check_grounding("Ograda od 25 m, visina 153 cm, 2 kuta.", facts()).ok


def test_price_next_to_an_on_request_line_fails_even_when_known() -> None:
    result = check_grounding("Potporni stup 100 cm je 53,00 KM.", facts())
    assert not result.ok


def test_price_for_gate_or_made_to_order_fails() -> None:
    assert not check_grounding("Kapija je 53,00 KM.", facts()).ok
    assert not check_grounding("Stupovi po narudžbi koštaju 24,90 KM.", facts()).ok
    assert not check_grounding("Montaža 53 KM.", facts()).ok


def test_phone_number_is_not_a_listing_id() -> None:
    assert check_grounding("Nazovite 036 651 427.", Facts()).ok


def test_empty_facts_reject_any_price() -> None:
    assert not check_grounding("Cijena je 10 KM.", Facts()).ok
