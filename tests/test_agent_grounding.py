from decimal import Decimal

import pytest

# ruff: noqa: RUF001
from martom.agent import copy
from martom.agent.grounding import (
    Facts,
    check_grounding,
    facts_from_output,
    numbers_in,
    parse_km,
    word_numbers,
)

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
    assert {Decimal(10), Decimal(11), Decimal(2)} <= {Decimal(n) for n in found.numbers}
    assert found.unpriced_names == ["Potporni stup 100 cm"]


def test_parse_km_reads_bosnian_number_formats() -> None:
    assert parse_km("53,00") == Decimal("53.00")
    assert parse_km("1.234,50") == Decimal("1234.50")
    assert parse_km("53") == Decimal("53")
    assert parse_km("24.90") == Decimal("24.90")
    assert parse_km("1 049,00") == Decimal("1049.00")
    assert parse_km("49,-") == Decimal("49")


def test_grounded_answer_passes() -> None:
    text = (
        "Za 25 m trebate 10 panela po 53,00 KM i 11 stupova (24,90 KM). "
        "Ukupno 803,90 KM. Panel: oglas 63996799."
    )
    assert check_grounding(text, facts(), known_numbers=[Decimal(25)]).ok


def test_price_not_from_a_tool_fails() -> None:
    result = check_grounding("Panel košta 49,00 KM.", facts())
    assert not result.ok
    assert "49,00 KM" in result.problems[0]


def test_unknown_listing_id_fails() -> None:
    assert not check_grounding("Pogledajte oglas 12345678.", facts()).ok


def test_quantity_not_from_a_tool_fails() -> None:
    assert not check_grounding("Trebate 12 panela.", facts()).ok


def test_customer_numbers_may_be_repeated() -> None:
    customer = [Decimal(25), Decimal(153), Decimal(2)]
    text = "Ograda od 25 m, visina 153 cm, 2 kuta."
    assert check_grounding(text, facts(), known_numbers=customer).ok
    assert not check_grounding(text, facts()).ok


def test_customer_number_is_never_a_price() -> None:
    assert not check_grounding("Ograda je 25 KM.", facts(), known_numbers=[Decimal(25)]).ok


# R1-1: every form the reviewer got past the old pattern list.
@pytest.mark.parametrize(
    "text",
    [
        "Panel je 49,- KM.",
        "Panel je 49,-.",
        "Panel košta KM 49,00.",
        "Panel košta BAM 49.",
        "Panel košta 49 maraka.",
        "Panel košta 49,00 konvertibilnih maraka.",
        "Cijena: 49 €.",
        "Cijena: € 53,00.",
        "Cijena: 53 EUR.",
        "Panel je 53 eura.",
        "Trebate 12 stubova.",
        "Trebate 12 vijaka i 3 kutije.",
        "Ukupno 1 049,00 KM.",
        "Uzmite 7 rola.",
    ],
)
def test_ungrounded_price_or_count_in_any_form_fails(text: str) -> None:
    assert not check_grounding(text, facts()).ok


@pytest.mark.parametrize(
    "text",
    [
        "Panel je KM 53,00.",
        "Panel je 53,- KM.",
        "Panel je 53 marke.",
        "Ukupno 803,90 KM za 10 panela i 11 stubova.",
        "Panel 153 × 250 cm, 53,00 KM za 1 kom.",
    ],
)
def test_grounded_price_or_count_in_any_form_passes(text: str) -> None:
    found = facts().merge(facts_from_output({"name": "Panel 153 × 250 cm"}))
    assert check_grounding(text, found).ok, check_grounding(text, found).problems


def test_space_grouped_thousands_are_one_amount() -> None:
    found = Facts(prices=["1049.00"])
    assert check_grounding("Ukupno 1 049,00 KM.", found).ok
    assert check_grounding("Ukupno 1\u00a0049,00 KM.", found).ok  # no-break space


def test_echoed_cart_quantity_is_not_a_fact() -> None:
    output = {"cart_line": {"listing_id": 1234567, "quantity": 13, "unit_price": "53.00"}}
    found = facts_from_output(output, skip_keys={"quantity"})
    assert not check_grounding("Dodao sam 13 panela u korpu.", found).ok


def test_copy_numbers_are_known() -> None:
    assert check_grounding("Rade se po narudžbi, 10–15 dana. Antracit (RAL 7016).", Facts()).ok


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


# R2-2: prices and counts written as Bosnian number words.
@pytest.mark.parametrize(
    "text",
    [
        "Panel košta pedeset maraka.",
        "Panel košta pedeset četiri KM.",
        "Cijena je četrdeset i devet KM.",
        "Cijena je KM pedeset.",
        "Panel je dvadeset eura.",
        "Trebate dvanaest stubova.",
        "Trebate dvadeset jedan panel.",
        "Uzmite dvije hiljade kopči.",
    ],
)
def test_number_words_are_checked_like_digits(text: str) -> None:
    assert not check_grounding(text, facts()).ok


@pytest.mark.parametrize(
    "text",
    [
        "Panel košta pedeset tri KM.",
        "Trebate deset panela i jedanaest stubova.",
        "Uzmite jedan panel za probu.",
        "Ukupno osamsto tri KM.",
    ],
)
def test_grounded_number_words_pass(text: str) -> None:
    found = facts().merge(Facts(prices=["803"]))
    assert check_grounding(text, found).ok, check_grounding(text, found).problems


def test_word_numbers_values() -> None:
    text = "četrdeset i devet, dvije hiljade, dva tri, sto dvadeset, sto se tice"
    assert [n.value for n in word_numbers(text)] == [49, 2000, 2, 3, 120]


def test_customer_number_words_are_known() -> None:
    assert Decimal(25) in numbers_in("Ograda od dvadeset pet metara")


# R2-3: a numbered recommendation list is not a set of quantities.
def test_numbered_list_passes() -> None:
    # The "3" of "3D" is a Retrieve-hit title number, as in the graph test.
    found = facts().merge(Facts(numbers=["3"]))
    text = "Imam dva prijedloga:\n1. PANEL 3D OGRADA\n2. Stup s pločom\n3. PVC kopče"
    assert check_grounding(text, found).ok, check_grounding(text, found).problems


def test_every_copy_text_passes() -> None:
    # The fixed copy may be quoted; its numbers are known only inside their phrases.
    texts = [
        value
        for name, value in vars(copy).items()
        if name.isupper() and isinstance(value, str) and "{" not in value
    ]
    for text in texts:
        assert check_grounding(text, Facts()).ok, (text, check_grounding(text, Facts()).problems)


@pytest.mark.parametrize(
    "text",
    [
        "Trebate 10 stubova.",
        "Trebate deset stubova.",
        "Trebate 15 panela.",
        "Trebate petnaest panela.",
        "Za 10 m trebate 15 panela.",
        "Trebate 36 panela.",
        "Trebate 7016 kopči.",
    ],
)
def test_copy_numbers_are_not_free_counts(text: str) -> None:
    # R3a-1: "10-15 dana" and "RAL 7016" don't make 10, 15 or 7016 a known quantity.
    assert not check_grounding(text, Facts()).ok


@pytest.mark.parametrize(
    "text", ["Trebate 20 panela.", "Trebate dvadeset panela.", "Trebate 312 stubova."]
)
def test_path_and_docstring_numbers_are_not_allowed(text: str) -> None:
    assert not check_grounding(text, Facts()).ok


# R3-2: invented prices in words the parser used to skip.
@pytest.mark.parametrize(
    "text",
    [
        "Panel košta sto KM.",
        "Panel košta sto pedeset KM.",
        "Panel košta sto i pedeset KM.",
        "Panel košta sto i pet KM.",
        "Panel košta sto i jedan KM.",
        "Panel košta pedesetak KM.",
        "Cijena je oko stotinjak maraka.",
        "Košta pola KM po komadu.",
        "To je par maraka.",
        "Košta nekoliko KM.",
    ],
)
def test_vague_or_sto_prices_fail(text: str) -> None:
    found = facts().merge(Facts(prices=["50"]))
    assert not check_grounding(text, found).ok


@pytest.mark.parametrize(
    "text",
    ["Sve cijene su u KM.", "Sto se tiče boje, imamo antracit.", "Panel je sto posto pocinčan."],
)
def test_currency_or_sto_without_a_price_passes(text: str) -> None:
    assert check_grounding(text, facts()).ok, check_grounding(text, facts()).problems


def test_sto_with_a_tool_price_passes() -> None:
    found = facts().merge(Facts(prices=["150"]))
    assert check_grounding("Ukupno sto pedeset KM.", found).ok
    assert check_grounding("Ukupno sto i pedeset KM.", found).ok
    assert check_grounding("Sto i to je sve.", Facts()).ok


def test_numbered_list_still_checks_the_numbers_inside() -> None:
    text = "1. Panel za 49 KM\n2. Trebate 12 stubova"
    problems = check_grounding(text, facts()).problems
    assert len(problems) == 2


def test_suggestion_count_over_ten_is_checked() -> None:
    assert not check_grounding("Imam 12 prijedloga.", facts()).ok


# R3a2-1: every number-word and currency check ignores case.
@pytest.mark.parametrize(
    "text",
    [
        "STO I PEDESET KM.",
        "Sto I pedeset KM.",
        "PANEL KOŠTA STO KM.",
        "PANEL KOŠTA STO PEDESET KM.",
        "CIJENA JE ČETRDESET I DEVET KM.",
        "PANEL KOŠTA PEDESET ČETIRI MARAKA.",
        "PANEL KOŠTA 49 MARAKA.",
        "PANEL KOŠTA 49,00 KONVERTIBILNIH MARAKA.",
        "CIJENA: 53 EUR.",
        "PANEL JE 53 EURA.",
        "PANEL KOŠTA bam 49.",
        "PANEL KOŠTA PEDESETAK KM.",
        "KOŠTA POLA KM.",
        "TREBATE DVANAEST STUBOVA.",
        "TREBATE 13 STUBOVA.",
    ],
)
def test_upper_case_prices_and_counts_fail(text: str) -> None:
    found = facts().merge(Facts(prices=["50"]))
    assert not check_grounding(text, found).ok


@pytest.mark.parametrize(
    "text",
    [
        "STO SE TIČE BOJE, IMAMO ANTRACIT.",
        "IMAM DVA PRIJEDLOGA.",
        "ROK JE 10–15 DANA.",
        "ANTRACIT (ral 7016).",
        "UKUPNO STO I PEDESET KM.",
        "PANEL JE 53,00 km.",
    ],
)
def test_upper_case_grounded_text_passes(text: str) -> None:
    found = facts().merge(Facts(prices=["150"]))
    assert check_grounding(text, found).ok, check_grounding(text, found).problems


# --- manual products (#132) -------------------------------------------------------------

MANUAL_OUTPUT = {
    "source": "manual",
    "listing_id": None,
    "manual_key": "anchor_m8x70",
    "title": "Anker M8×70",
    "url": None,
    "price": "0.80",
}


def test_facts_collect_manual_keys_and_prices() -> None:
    facts = facts_from_output(MANUAL_OUTPUT)
    assert facts.manual_keys == ["anchor_m8x70"]
    assert facts.prices == ["0.80"]
    assert facts.listing_ids == []


def test_manual_price_from_a_tool_passes() -> None:
    facts = facts_from_output(MANUAL_OUTPUT)
    assert check_grounding("Anker M8×70 je 0,80 KM po komadu.", facts).ok


def test_manual_price_not_from_a_tool_fails() -> None:
    facts = facts_from_output(MANUAL_OUTPUT)
    assert not check_grounding("Anker M8×70 je 0,85 KM.", facts).ok


def test_manual_key_from_a_tool_passes_like_a_listing_id() -> None:
    facts = facts_from_output(MANUAL_OUTPUT)
    assert check_grounding("Šifra: anchor_m8x70.", facts).ok


def test_unknown_manual_key_fails_like_a_listing_id() -> None:
    facts = facts_from_output(MANUAL_OUTPUT)
    result = check_grounding("Šifra: anchor_m10x90.", facts)
    assert not result.ok
    assert any("anchor_m10x90" in problem for problem in result.problems)


# --- links (#132 R1-1) ---------------------------------------------------------------

OLX_OUTPUT = {
    "results": [
        {
            "source": "olx",
            "listing_id": 63996799,
            "title": "PANEL 3D OGRADA",
            "url": "https://martom.olx.ba/artikal/panel-3d-ograda-63996799",
        },
        MANUAL_OUTPUT,
    ]
}


def test_facts_collect_tool_urls() -> None:
    facts = facts_from_output(OLX_OUTPUT)
    assert facts.urls == ["https://martom.olx.ba/artikal/panel-3d-ograda-63996799"]


PANEL_URL = "https://martom.olx.ba/artikal/panel-3d-ograda-63996799"


@pytest.mark.parametrize(
    "text",
    [
        f"Panel: {PANEL_URL}",
        f"Panel: {PANEL_URL}.",
        f"Panel ({PANEL_URL}).",
        # A1-1: the same link in the wrappers a model writes.
        f"Panel: **{PANEL_URL}**",
        f"Panel: <{PANEL_URL}>",
        f"Panel: `{PANEL_URL}`",
        f"Panel: „{PANEL_URL}“",
        f"Panel: “{PANEL_URL}”",
        f"Panel: '{PANEL_URL}'",
        f"Panel: {PANEL_URL}…",
        f"[PANEL 3D OGRADA]({PANEL_URL})",
        f"[PANEL 3D OGRADA]({PANEL_URL}).",
        f"[{PANEL_URL}]({PANEL_URL})",
        f"**[PANEL 3D OGRADA]({PANEL_URL})**",
    ],
)
def test_link_a_tool_returned_passes(text: str) -> None:
    facts = facts_from_output(OLX_OUTPUT)
    result = check_grounding(text, facts)
    assert result.ok, result.problems


@pytest.mark.parametrize(
    "text",
    [
        "Sve artikle imamo na OLX.ba.",
        "Pogledajte oglas na olx.ba ili nas nazovite.",
        "Martom je na OLX.ba, a i u Međugorju.",
    ],
)
def test_naming_olx_ba_without_a_link_passes(text: str) -> None:
    # A1-1: the site's name alone is not a link.
    assert check_grounding(text, facts_from_output(OLX_OUTPUT)).ok


@pytest.mark.parametrize(
    "text",
    [
        # A manual product has no OLX listing: any link for it is invented.
        "Anker: https://martom.olx.ba/artikal/anker-m8x70",
        "Anker: martom.olx.ba/artikal/anchor_m8x70",
        "Anker: olx.ba/artikal/anker-m8x70",
        "Anker: www.olx.ba/artikal/anker",
        # An OLX link no tool returned, even for a known listing id.
        "Panel: https://olx.ba/artikal/63996799",
        "Panel: https://martom.olx.ba/artikal/drugi-panel-63996799",
        "Pogledajte https://example.com/anker",
        # Wrappers never turn a wrong link into a right one.
        "Anker: **https://martom.olx.ba/artikal/anker-m8x70**",
        "Anker: <martom.olx.ba/artikal/anker-m8x70>",
        "[Anker](https://martom.olx.ba/artikal/anker-m8x70)",
        f"[{PANEL_URL}](https://martom.olx.ba/artikal/anker-m8x70)",
        f"Panel: `{PANEL_URL}/`",
    ],
)
def test_link_no_tool_returned_fails(text: str) -> None:
    facts = facts_from_output(OLX_OUTPUT)
    result = check_grounding(text, facts)
    assert not result.ok
    assert any("link" in problem for problem in result.problems)


# --- #134 follow-ups ------------------------------------------------------------------


# Task 1: a suggestion count is exempt only when no product noun follows the suggestion noun.
@pytest.mark.parametrize(
    "text",
    [
        "Trebate 7 varijanti panela.",
        "Trebate pet opcija stubova.",
        "Uzmite 3 varijante stupova.",
        "Trebate dvije opcije kopči.",
        "IMAM DVA PRIJEDLOGA PANELA.",
        "Imam 12 prijedloga.",
    ],
)
def test_suggestion_noun_before_a_product_noun_is_checked(text: str) -> None:
    # No facts: facts() knows the BOM quantity 2, which would ground "dva"/"dvije".
    result = check_grounding(text, Facts())
    assert not result.ok


@pytest.mark.parametrize(
    "text",
    [
        "Imam dva prijedloga.",
        "Imam 2 prijedloga:",
        "Imam dva prijedloga:\n- PANEL\n- Stup",
        "Imam dvije opcije za vašu ogradu.",
        "Postoje tri načina, kako želite.",
        "Imam 2 prijedloga, oba su dostupna.",
        "Imam dva prijedloga koja odgovaraju.",
    ],
)
def test_suggestion_count_without_a_product_noun_passes(text: str) -> None:
    result = check_grounding(text, facts())
    assert result.ok, result.problems


# Task 2: a list marker never hides a number with a currency after it.
@pytest.mark.parametrize(
    "text",
    [
        "Ukupno:\n12. KM",
        "Ukupno:\n12) KM",
        "Ukupno:\n12.\nKM",
        "Ukupno:\n  12. maraka",
        "Ukupno:\n12. €",
    ],
)
def test_list_marker_before_a_currency_is_a_price(text: str) -> None:
    # 12 is a customer number here, so only the price check can catch it.
    result = check_grounding(text, facts(), known_numbers=[Decimal(12)])
    assert not result.ok
    assert any("price" in problem for problem in result.problems)


def test_list_marker_before_a_known_price_passes() -> None:
    result = check_grounding("Ukupno:\n53. KM", facts())
    assert result.ok, result.problems


# Task 3: rough counts of products in words fail (quantities come only from tools).
@pytest.mark.parametrize(
    "text",
    [
        "Trebate desetak stubova.",
        "Trebate dvadesetak panela.",
        "Uzmite par stubova.",
        "Trebate petnaestak dodatnih kopči.",
        "Treba vam stotinjak vijaka.",
        "TREBATE DESETAK STUBOVA.",
    ],
)
def test_rough_count_of_products_fails(text: str) -> None:
    result = check_grounding(text, facts(), known_numbers=[Decimal(10), Decimal(20)])
    assert not result.ok
    assert any("rough count" in problem for problem in result.problems)


@pytest.mark.parametrize(
    "text",
    [
        "Imam par prijedloga.",
        "Isporuka je za par dana.",
        "Još nekoliko podataka:",
        "Jak stup drži panel.",
        "Desetak minuta i gotovo.",
    ],
)
def test_rough_count_of_suggestions_or_time_passes(text: str) -> None:
    result = check_grounding(text, facts())
    assert result.ok, result.problems


# A1-2 (#132 review): links without a scheme on any domain are checked too.
@pytest.mark.parametrize(
    "text",
    [
        "Pogledajte martomshop.ba/anker",
        "Pogledajte example.com/path",
        "Pogledajte shop.example.co.uk/anker-m8x70.",
        "Pogledajte **martomshop.ba/proizvod/anker**",
    ],
)
def test_scheme_less_link_on_any_domain_fails(text: str) -> None:
    result = check_grounding(text, facts_from_output(OLX_OUTPUT))
    assert not result.ok
    assert any("link" in problem for problem in result.problems)


def test_scheme_less_link_a_tool_returned_passes() -> None:
    found = Facts(urls=["martomshop.ba/anker"])
    result = check_grounding("Pogledajte martomshop.ba/anker.", found)
    assert result.ok, result.problems


@pytest.mark.parametrize(
    "text",
    [
        "Imamo i web shop martomshop.ba.",
        "Pišite na info@martom-bekaert.com.",
        "Pogledajte example.com ili nas nazovite.",
        "Panel je 24.90 KM/kom.",
        "Cijena po m/kom je ista.",
    ],
)
def test_bare_domain_or_slash_text_is_not_a_link(text: str) -> None:
    result = check_grounding(text, facts())
    assert result.ok, result.problems


# --- #134 review round 1 -----------------------------------------------------------------


# R1-1: a list marker is a price only when a currency follows directly and no number after it.
@pytest.mark.parametrize(
    "text",
    [
        "1. Eurofence panel\n2. Stup",
        "1. EURO PANEL ZELENI\n2. Stup",
        "1. KM 53,00 za panel\n2. Stup",
        "1. KM 53,00",
        "1) Eurofence panel",
    ],
)
def test_list_marker_before_a_product_or_a_currency_first_price_passes(text: str) -> None:
    result = check_grounding(text, Facts(prices=["53.00"]))
    assert result.ok, result.problems


def test_list_marker_before_a_currency_first_price_still_checks_the_price() -> None:
    result = check_grounding("1. KM 49,00", Facts(prices=["53.00"]))
    assert not result.ok
    assert all("'1" not in problem for problem in result.problems)


@pytest.mark.parametrize("text", ["Ukupno:\n12. eura", "Ukupno:\n12) EUR", "Ukupno:\n12. eurima"])
def test_list_marker_before_a_euro_is_a_euro_price(text: str) -> None:
    result = check_grounding(text, Facts(prices=["12"]))
    assert not result.ok
    assert any("euros" in problem for problem in result.problems)


# R1-2: a rough count passes before any word that is not a product or material.
@pytest.mark.parametrize(
    "text",
    [
        "Evo par savjeta:",
        "Imam par pitanja:",
        "Samo par napomena:",
        "U par koraka do ograde.",
        "Par stvari za provjeriti:",
        "Imam još par informacija.",
        "Par rukavica uvijek dobro dođe.",
        "Desetak godina traje.",
        "Za par minuta.",
        "Par savjeta za stubove:",
    ],
)
def test_rough_count_of_non_products_passes(text: str) -> None:
    result = check_grounding(text, Facts())
    assert result.ok, result.problems


@pytest.mark.parametrize(
    "text",
    [
        "Trebate par metara žice.",
        "Uzmite desetak ankera.",
        "Treba vam par rola.",
        "Dodajte dvadesetak komada.",
        "Uzmite par zatezača.",
    ],
)
def test_rough_count_of_materials_fails(text: str) -> None:
    result = check_grounding(text, Facts())
    assert not result.ok


# R1-3: the suggestion exemption holds before prepositions and other non-product words.
@pytest.mark.parametrize(
    "text",
    [
        "Evo tri opcije iz naše ponude:",
        "Imam dva prijedloga u nastavku:",
        "Imam dvije opcije s različitim visinama.",
        "Postoje dva načina na koja možete:",
        "Imam 3 prijedloga od kojih je prvi najjeftiniji.",
        "Evo tri varijante boje: zelena, antracit i bijela.",
        "Imam dva prijedloga za stubove.",
    ],
)
def test_suggestion_count_before_a_non_product_word_passes(text: str) -> None:
    result = check_grounding(text, Facts())
    assert result.ok, result.problems


@pytest.mark.parametrize(
    "text",
    ["Imam tri prijedloga zelenih panela.", "Postoje 4 varijante ankera.", "Imam 2 opcije mreže."],
)
def test_suggestion_count_before_a_product_noun_is_checked(text: str) -> None:
    assert not check_grounding(text, Facts()).ok


# R1-4: the problem quotes the words without punctuation.
def test_rough_count_problem_has_no_punctuation() -> None:
    result = check_grounding("Uzmite par stubova.", Facts())
    assert "rough count 'par stubova'" in result.problems[0]


def test_eurofence_title_with_a_number_is_not_a_euro_price() -> None:
    found = Facts(numbers=["3"])
    for text in ["Preporučujem Eurofence 3D.", "EUROFENCE 3D panel je dostupan."]:
        assert check_grounding(text, found).ok, check_grounding(text, found).problems
    assert not check_grounding("Panel je 3 eura.", found).ok
    assert not check_grounding("Panel je euro 3.", found).ok


# --- #134 review round 2 -----------------------------------------------------------------


# R2-1: only an amount right after the currency makes "1. KM" a currency-first price.
@pytest.mark.parametrize(
    "text",
    [
        "Ukupno:\n85. KM (2 panela)",
        "Ukupno:\n85. KM za 2 panela",
        "Ukupno:\n85) KM, 2 panela",
        "Ukupno:\n85. KM\n2. Stup",
    ],
)
def test_marker_price_with_a_number_later_on_the_line_is_checked(text: str) -> None:
    result = check_grounding(text, Facts(numbers=["2"]))
    assert not result.ok
    assert any("85" in problem and "price" in problem for problem in result.problems)


@pytest.mark.parametrize("text", ["1. KM 53,00", "1. KM 53,00 za panel", "1. KM: 53,00"])
def test_marker_before_a_currency_first_price_still_passes(text: str) -> None:
    result = check_grounding(text, Facts(prices=["53.00"]))
    assert result.ok, result.problems


# R2-2: BOM items and catalogue hardware are products, hyphenated forms too.
@pytest.mark.parametrize(
    "text",
    [
        "Trebate desetak T-stupova.",
        "Trebate 7 varijanti T-stupova.",
        "Trebate par potpornih T-stupova.",
        "Trebate par španera.",
        "Trebate par spanera.",
        "Imam pet opcija španera.",
        "Trebate desetak nosača.",
        "Trebate desetak nosaca.",
        "Uzmite par koluta.",
        "Trebate par šarki.",
        "Trebate desetak spojnica.",
        "Trebate desetak obujmica.",
        "Trebate par kapica.",
        "Trebate desetak tipli.",
        "Trebate par brava.",
        "Trebate desetak metara pletiva.",
    ],
)
def test_rough_or_suggestion_count_of_bom_items_fails(text: str) -> None:
    assert not check_grounding(text, Facts()).ok


def test_every_bom_line_name_is_a_product_noun() -> None:
    # A calculator line whose noun isn't in _PRODUCT_NOUN would let "desetak <it>" pass.
    from bom_lines import all_bom_lines

    missing = set()
    for name in {line.name for line in all_bom_lines()}:
        result = check_grounding(f"Trebate desetak {name}.", Facts())
        if not any("rough count" in problem for problem in result.problems):
            missing.add(name)
    assert missing == set()
