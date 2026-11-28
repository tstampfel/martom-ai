"""The one scrub every promotion of a real chat to an eval set goes through (#129).

Every phone number, name and address here is fake (060 000 000 style digits, invented
names and streets): this repo is public.
"""

import pytest

from martom.evals.scrub import scrub_personal_data

# --- phones -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phone",
    [
        "061 000 000",  # BiH mobile
        "061/000-000",
        "062-000-000",
        "063 000 0000",
        "065000000",
        "(061) 000-000",
        "061.000.000",
        "061 / 000 000",
        "+387 61 000 000",
        "+387 (0)61 000 000",
        "00387 61 000 000",
        "387 61 000 000",
        "+38761000000",
        "033 000 000",  # BiH landline
        "036/000-000",  # Mostar area, not the shop's number
        "(036) 000 000",
        "+385 91 000 0000",  # HR mobile
        "091 000 0000",
        "098 0000 000",
        "01 0000 000",  # Zagreb landline
        "+385 1 0000 000",
        "00385 98 000 0000",
    ],
)
def test_removes_bih_and_hr_phone_numbers(phone: str) -> None:
    scrubbed = scrub_personal_data(f"Nazovite me na {phone} poslije 16h.")

    assert scrubbed == "Nazovite me na [telefon] poslije 16h."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("moj broj je 61 000 000", "moj broj je [telefon]"),
        ("Tel: 61/000-000", "Tel: [telefon]"),
        ("mob. 91 000 0000", "mob. [telefon]"),
    ],
)
def test_removes_a_number_without_its_leading_zero_after_a_phone_word(
    text: str, expected: str
) -> None:
    assert scrub_personal_data(text) == expected


@pytest.mark.parametrize("shop", ["036 651 427", "036/651-427", "+387 36 651 427", "(036) 651 427"])
def test_keeps_the_shops_public_number(shop: str) -> None:
    text = f"Pozovite trgovinu na {shop}."

    assert scrub_personal_data(text) == text


# Product text from assistant replies and customer messages (R1-1): none of it is a
# phone, a name or an address, so it must come out unchanged.
@pytest.mark.parametrize(
    "text",
    [
        "Ograda 25 m, panel 153 cm, antracit, 12 stubova.",
        "Panel 153 x 2500 mm košta 54,90 KM.",
        "Artikal 61234567 na OLX-u.",  # a listing id, not a phone
        "Ukupno 1.234,50 KM za 2026. godinu.",
        "Visina H-100, razmak 250 cm, RAL 7016.",
        "Dostava za 10-15 dana.",
        "Panel Nylofor 3D 153 cm",
        "Uzmite Panel Nylofor 3D.",
        "Panel Fortinet Medium 153",
        "Ograda Bekafor Classic 150",
        "Žica Pantanet Family 25",
        "Kolica Tačke 85 L",
        "Panel Zeleni 3.",
        "Stub Antracit 60/40 2000",
        "Datum 01/12/2026",
        "Narudžba od 01.12.2026.",
        "Rok 02-12-2026",
        "Panel 153x250, anker M8x70, vijak M10\u00d780.",
        "Uzmite Panel Zeleni 3 komada i Stub Antracit 4 kom.",
        "Panel Antracit 153 x 2500 mm, Eurofence Antracit 25 m.",
        "Panel Antracit 153\u00d72500 mm.",  # the multiplication sign
        "Prvi put kupujem 3 panela.",
        "Kontakt trgovine: 036 651 427.",
        "Treba vam dostava na 3 adrese? Ne, samo panel u boji Antracit 153.",
        "Cijena 1290 KM, artikal 51234567, 7 komada.",
        "Ja sam iz Čitluka, trebam ogradu.",
        "Broj artikla je 61234567.",
    ],
)
def test_leaves_product_text_sizes_prices_ids_and_dates_alone(text: str) -> None:
    assert scrub_personal_data(text) == text


# --- e-mail -------------------------------------------------------------------------


def test_removes_customer_email_but_keeps_martoms() -> None:
    text = "Pišite na pero.peric@example.com ili zeljezarija@martom-bekaert.com."

    assert scrub_personal_data(text) == "Pišite na [email] ili zeljezarija@martom-bekaert.com."


# --- names --------------------------------------------------------------------------


def test_removes_the_names_given_in_the_order_form() -> None:
    text = "Pero Perić: hoću ogradu. Pozdrav, Pero"

    scrubbed = scrub_personal_data(text, names=["Pero Perić"])

    assert scrubbed == "[ime] [ime]: hoću ogradu. Pozdrav, [ime]"


def test_name_matching_ignores_accents_case_and_bosnian_endings() -> None:
    text = "Poslao sam pericu i PERU poruku, Perićeva ograda."

    scrubbed = scrub_personal_data(text, names=["Pero Perić"])

    assert scrubbed == "Poslao sam [ime] i [ime] poruku, [ime] ograda."


@pytest.mark.parametrize(
    ("form_name", "typed"),
    [
        ("Đuro Đurić", "Djuro Djurić"),
        ("Đuro Đurić", "Duro Duric"),
        ("Djuro Djurić", "Đuro Đurić"),
    ],
)
def test_dj_and_d_match_the_letter_dj(form_name: str, typed: str) -> None:
    scrubbed = scrub_personal_data(f"Pozdrav, {typed}.", names=[form_name])

    assert scrubbed == "Pozdrav, [ime] [ime]."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Zovem se Pero Perić, trebam ogradu.", "Zovem se [ime], trebam ogradu."),
        ("zovem se pero perić i trebam ogradu", "zovem se [ime] i trebam ogradu"),
        ("Zovem se PERO PERIĆ.", "Zovem se [ime]."),
        ("Moje ime je Ana.", "Moje ime je [ime]."),
        ("ime mi je ana", "ime mi je [ime]"),
        ("Ja sam Ana Anić.", "Ja sam [ime]."),
        ("Ime i prezime: Ante Antić", "Ime i prezime: [ime]"),
        ("ime: Mara Marić\nTelefon: 060 000 000", "ime: [ime]\nTelefon: [telefon]"),
    ],
)
def test_removes_names_the_customer_introduces(text: str, expected: str) -> None:
    assert scrub_personal_data(text) == expected


# --- addresses ----------------------------------------------------------------------


def test_removes_the_address_given_in_the_order_form() -> None:
    text = "Dostava na Izmišljena ulica 12, Čitluk molim."

    scrubbed = scrub_personal_data(text, addresses=["Izmišljena ulica 12"])

    assert scrubbed == "Dostava na [adresa], Čitluk molim."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Adresa: Kralja Izmišljenog 12, Čitluk", "Adresa: [adresa]"),
        # The label stops at the end of the sentence or a tag (R1-3).
        (
            "Adresa: Kralja Izmišljenog 12, Čitluk. Trebam i 3 panela.",
            "Adresa: [adresa]. Trebam i 3 panela.",
        ),
        ("Adresa: Ul. Izmišljena bb, Čitluk. Hvala!", "Adresa: [adresa]. Hvala!"),
        (
            "<p>Adresa: Izmišljena 5</p><p>Panel 153</p>",
            "<p>Adresa: [adresa]</p><p>Panel 153</p>",
        ),
        ("Živim u ulici Izmišljenoj 5a.", "Živim u [adresa]."),
        ("Ul. Izmišljena bb, Mostar", "[adresa], Mostar"),
        ("Trg izmišljenih 3", "[adresa]"),
        ("Dostava na Kralja Izmišljenog 23, 88000 Mostar.", "Dostava na [adresa], [adresa]."),
        ("dostava na kralja izmišljenog 12 citluk", "dostava na [adresa] citluk"),
        ("Pošaljite na Izmišljena 7, hvala.", "Pošaljite na [adresa], hvala."),
        ("Stanujem u Izmišljenoj 4b.", "Stanujem u [adresa]."),
        ("Kuća je Izmišljena bb.", "Kuća je [adresa]."),
    ],
)
def test_removes_street_addresses(text: str, expected: str) -> None:
    assert scrub_personal_data(text) == expected


# --- whole records ------------------------------------------------------------------


def test_scrubs_every_string_in_a_promoted_record() -> None:
    record = {
        "id": "chat-7",
        "turns": [
            {"role": "user", "content": "Zovem se Pero Perić, 060 000 000"},
            {"role": "assistant", "content": "Hvala, Pero! Ograda 25 m."},
        ],
        "sides": [10, 15],
        "closed": True,
    }

    scrubbed = scrub_personal_data(record, names=["Pero Perić"])

    assert scrubbed == {
        "id": "chat-7",
        "turns": [
            {"role": "user", "content": "Zovem se [ime], [telefon]"},
            {"role": "assistant", "content": "Hvala, [ime]! Ograda 25 m."},
        ],
        "sides": [10, 15],
        "closed": True,
    }
    # A copy: the original record is left as it was.
    assert record["turns"][0]["content"] == "Zovem se Pero Perić, 060 000 000"  # type: ignore[index]


def test_scrubbing_twice_changes_nothing_more() -> None:
    once = scrub_personal_data("Zovem se Pero, 060 000 000, Adresa: Izmišljena 1")

    assert scrub_personal_data(once) == once
