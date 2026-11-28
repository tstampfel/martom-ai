"""The one scrub every promotion of a real chat to an eval set goes through (#129)."""

import pytest

from martom.evals.scrub import scrub_personal_data

# --- phones -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phone",
    [
        "061 234 567",  # BiH mobile
        "061/234-567",
        "062-345-678",
        "063 123 4567",
        "065234567",
        "+387 61 234 567",
        "+387 (0)61 234 567",
        "00387 61 234 567",
        "+38761234567",
        "033 123 456",  # BiH landline (Sarajevo)
        "036/123-456",  # Mostar area, not the shop's number
        "+385 91 234 5678",  # HR mobile
        "091 234 5678",
        "098 1234 567",
        "01 2345 678",  # Zagreb landline
        "+385 1 2345 678",
        "00385 98 765 4321",
    ],
)
def test_removes_bih_and_hr_phone_numbers(phone: str) -> None:
    scrubbed = scrub_personal_data(f"Nazovite me na {phone} poslije 16h.")

    assert scrubbed == "Nazovite me na [telefon] poslije 16h."


@pytest.mark.parametrize("shop", ["036 651 427", "036/651-427", "+387 36 651 427"])
def test_keeps_the_shops_public_number(shop: str) -> None:
    text = f"Pozovite trgovinu na {shop}."

    assert scrub_personal_data(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Ograda 25 m, panel 153 cm, antracit, 12 stubova.",
        "Panel 153 x 2500 mm košta 54,90 KM.",
        "Artikal 61234567 na OLX-u.",  # a listing id, not a phone
        "Ukupno 1.234,50 KM za 2026. godinu.",
        "Visina H-100, razmak 250 cm, RAL 7016.",
        "Dostava za 10-15 dana.",
        "Uzmite Panel Zeleni 3 komada i Stub Antracit 4 kom.",
        "Panel Antracit 153 x 2500 mm, Eurofence Antracit 25 m.",
        "Prvi put kupujem 3 panela.",
        "Panel Antracit 153\u00d72500 mm.",  # the multiplication sign
        "Kontakt trgovine: 036 651 427.",
    ],
)
def test_leaves_fence_specs_prices_and_ids_alone(text: str) -> None:
    assert scrub_personal_data(text) == text


# --- e-mail -------------------------------------------------------------------------


def test_removes_customer_email_but_keeps_martoms() -> None:
    text = "Pišite na ivan.horvat@gmail.com ili zeljezarija@martom-bekaert.com."

    assert scrub_personal_data(text) == "Pišite na [email] ili zeljezarija@martom-bekaert.com."


# --- names --------------------------------------------------------------------------


def test_removes_the_names_given_in_the_order_form() -> None:
    text = "Ivan Šimić: hoću ogradu. Pozdrav, Ivan"

    scrubbed = scrub_personal_data(text, names=["Ivan Šimić"])

    assert scrubbed == "[ime] [ime]: hoću ogradu. Pozdrav, [ime]"


def test_name_matching_ignores_accents_case_and_bosnian_endings() -> None:
    text = "Poslao sam simicu i IVANU poruku, Šimićeva ograda."

    scrubbed = scrub_personal_data(text, names=["Ivan Šimić"])

    assert "simic" not in scrubbed.lower()
    assert "ivan" not in scrubbed.lower()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Zovem se Marko Kovač, trebam ogradu.", "Zovem se [ime], trebam ogradu."),
        ("Moje ime je Ana.", "Moje ime je [ime]."),
        ("Ime i prezime: Ante Perić", "Ime i prezime: [ime]"),
        ("ime: Mirela Babić\nTelefon: 061 234 567", "ime: [ime]\nTelefon: [telefon]"),
    ],
)
def test_removes_names_the_customer_introduces(text: str, expected: str) -> None:
    assert scrub_personal_data(text) == expected


def test_a_lowercase_word_after_an_intro_is_not_a_name() -> None:
    text = "Ja sam iz Čitluka, zovem se kasnije."

    assert scrub_personal_data(text) == text


# --- addresses ----------------------------------------------------------------------


def test_removes_the_address_given_in_the_order_form() -> None:
    text = "Dostava na Kralja Tomislava 12, Čitluk molim."

    scrubbed = scrub_personal_data(text, addresses=["Kralja Tomislava 12"])

    assert scrubbed == "Dostava na [adresa], Čitluk molim."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Adresa: Kralja Tomislava 12, Čitluk", "Adresa: [adresa]"),
        ("Živim u ulici Stjepana Radića 5a.", "Živim u [adresa]."),
        ("Ul. Bana Jelačića bb, Mostar", "[adresa], Mostar"),
        ("Dostava na Kralja Zvonimira 23, 88260 Čitluk.", "Dostava na [adresa], [adresa]."),
        ("Trg slobode 3", "[adresa]"),
    ],
)
def test_removes_street_addresses(text: str, expected: str) -> None:
    assert scrub_personal_data(text) == expected


# --- whole records ------------------------------------------------------------------


def test_scrubs_every_string_in_a_promoted_record() -> None:
    record = {
        "id": "chat-7",
        "turns": [
            {"role": "user", "content": "Zovem se Ivan Šimić, 061 234 567"},
            {"role": "assistant", "content": "Hvala, Ivan! Ograda 25 m."},
        ],
        "sides": [10, 15],
        "closed": True,
    }

    scrubbed = scrub_personal_data(record, names=["Ivan Šimić"])

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
    assert record["turns"][0]["content"] == "Zovem se Ivan Šimić, 061 234 567"  # type: ignore[index]


def test_scrubbing_twice_changes_nothing_more() -> None:
    once = scrub_personal_data("Zovem se Ivan, 061 234 567, Adresa: Glavna 1")

    assert scrub_personal_data(once) == once
