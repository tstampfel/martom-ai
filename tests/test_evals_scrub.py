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
        "01 31 0000",  # a Zagreb-style number that a loose date rule would let through
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
        ("broj telefona je 61000000", "broj telefona je [telefon]"),
        ("Kontakt: 61 000 000", "Kontakt: [telefon]"),
        ("broj je 61/000-000", "broj je [telefon]"),
        # Only the listing words themselves keep a number, not words that start like them.
        ("oglasio broj 61 000 000", "oglasio broj [telefon]"),
        ("Kontakt 61000000", "Kontakt [telefon]"),
        ("kontakt telefon: 61000000", "kontakt telefon: [telefon]"),
        # A phone word wins over a date-like shape.
        ("tel 61 12 2026", "tel [telefon]"),
    ],
)
def test_removes_a_number_without_its_leading_zero_after_a_phone_word(
    text: str, expected: str
) -> None:
    assert scrub_personal_data(text) == expected


# A listing id is 7-9 digits, the shape of a phone without its 0 (R2-1): after a bare
# "broj" or a listing word it stays.
@pytest.mark.parametrize(
    "text",
    [
        "artikal broj 61234567",
        "oglas broj 61234567",
        "šifra 61234567",
        "iz oglasa broj 61 234 567",
        "sifre 61 234 567",
        "broj 51234567 je artikal",
        "broj je 61234567",
        "Kontakt artikal 612345678",
        "Link: https://martom.olx.ba/artikal/panel-nylofor-3d-61234567",
    ],
)
def test_keeps_listing_ids_after_broj_and_listing_words(text: str) -> None:
    assert scrub_personal_data(text) == text


# Accepted over-scrub (Tomislav, fail closed): a capital M, L or H after a place reads
# as a building letter, so the place and the number go. The function only removes text.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DOSTAVA U MOSTAR 20 M OGRADE", "DOSTAVA U [adresa] OGRADE"),
        ("dostava na gradilište 5 L BOJE", "dostava na [adresa] BOJE"),
        ("dostava u Mostar 20 M ograde treba", "dostava u [adresa] ograde treba"),
    ],
)
def test_over_scrubs_all_caps_sizes_after_a_place(text: str, expected: str) -> None:
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
        "Datum 03 12 2026",
        "Datum 3 1 2027",
        "dostava u Mostar 3 dana",
        "dostava u Mostar 15. decembra",
        "dostava u Mostar 2 sata",
        "dostava u Mostar 2 sedmice",
        "dostava u Mostar 1 tjedan",
        "dostava u Mostar 30 minuta",
        "dostava u Mostar 3 dana, 2. marta",
        "dostava u Mostar 5-7 dana",
        "dostava u Mostar 2 - 3 sata",
        "dostava na gradilište 60/400",
        # A spaced unit or "x" is never a house-number letter (R5-1), in capitals too.
        # With no street word or delivery verb before them, capital units stay.
        "5 L BOJE",
        "Mostar 20 M ograde treba",
        "dostava u Mostar 20 m ograde",
        "dostava na kuću 15 m ograde",
        "dostava u Čitluk 40 m žice i 10 stubova",
        "dostava na gradilište 30 m žice",
        "dostava na gradilište 153 x 250",
        "dostava na kuću 3 x 2 m",
        "dostava u Mostar 12 X 3",
        "dostava na gradilište 5 l boje",
        "Ulica Izmišljena 5 m",
        "dostava na gradilište 1/2 cola",
        # Sizes with a slash stay (R4-1).
        "dostava na gradilište 153/250",
        "dostava na gradilište 60/40 cm",
        "dostava na anker M8/80",
        "dostava na gradilište stub 50/50 mm",
        "dostava na gradilište 2,5/3 m",
        "Panel 153/250",
        "Stub 60/40 cm",
        "Anker M8/80",
        "Profil 50/50",
        "dostava na gradilište stub 60/40/2000",
        "Mreža 2,5/3 m",
        "dostava na gradilište 4 palete",
        "dostava na gradilište 3 stuba i 2 panela",
        "dostava na gradilište 5 komada",
        "dostava na gradilište 12 metara",
        "dostava na gradilište 12 %",
        "dostava u Zagreb 1. siječnja",
        "dostava u Zagreb 7. svibnja",
        "dostava u Mostar 1. jula",
        "Šaljemo na gradilište za 2 sata.",
        "Kod 12345 Panel",
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
        ("Zovem se Ana Marija Anić.", "Zovem se [ime]."),
        ("zovem se ana marija anić i trebam ogradu", "zovem se [ime] i trebam ogradu"),
        ("Ime: Ana M. Anić, Mostar", "Ime: [ime], Mostar"),
        ("Ime i prezime: A. Anić. Trebam panel.", "Ime i prezime: [ime]. Trebam panel."),
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
        # A sentence end after the house number is not a date (". Hvala", ". hvala").
        ("Pošaljite na Izmišljena 7. Hvala.", "Pošaljite na [adresa]. Hvala."),
        ("dostava na izmišljena 7. hvala", "dostava na [adresa]. hvala"),
        # Words that only start like a time, unit or month word don't hide the address
        # (R3-1): exclusions are whole words in their real forms, never open prefixes.
        ("Može li dostava na Izmišljena 12 danas?", "Može li dostava na [adresa] danas?"),
        ("Pošaljite na Izmišljena 7 danas.", "Pošaljite na [adresa] danas."),
        ("Adresa je Ulica Izmišljena 12 danas", "Adresa je [adresa] danas"),
        ("Pošaljite na Izmišljena 7. Martom je super.", "Pošaljite na [adresa]. Martom je super."),
        ("Pošaljite na Izmišljena 7. Majstor dolazi.", "Pošaljite na [adresa]. Majstor dolazi."),
        ("Pošaljite na Izmišljena 9. Julija", "Pošaljite na [adresa]. Julija"),
        ("Pošaljite na Izmišljena 7 Danilovgrad", "Pošaljite na [adresa] Danilovgrad"),
        ("Pošaljite na Izmišljena 5 Stubica", "Pošaljite na [adresa] Stubica"),
        ("Pošaljite na Izmišljena 5 Komšiluk", "Pošaljite na [adresa] Komšiluk"),
        ("Pošaljite na Izmišljena 5 Metropol", "Pošaljite na [adresa] Metropol"),
        ("Pošaljite na Izmišljena 5 Satelit", "Pošaljite na [adresa] Satelit"),
        ("Pošaljite na Izmišljena 5 Minel", "Pošaljite na [adresa] Minel"),
        ("Pošaljite na Izmišljena 5 Hrasno", "Pošaljite na [adresa] Hrasno"),
        ("Pošaljite na Izmišljena 3. Septembarska", "Pošaljite na [adresa]. Septembarska"),
        ("Stanujem u Izmišljenoj 4 Lipanjska", "Stanujem u [adresa] Lipanjska"),
        ("dostava na izmišljena 5 xanadu", "dostava na [adresa] xanadu"),
        # Entrance or flat after a slash (R4-1), and other house-number forms.
        ("Pošaljite na Izmišljena 5/2", "Pošaljite na [adresa]"),
        ("Ulica Izmišljena 5/2, Čitluk", "[adresa], Čitluk"),
        ("Adresa je ulica Izmišljena 24/14", "Adresa je [adresa]"),
        ("dostava na Izmišljena 12/4 sutra", "dostava na [adresa] sutra"),
        ("Ulica Izmišljena 7 / 3", "[adresa]"),
        ("stanujem u Izmišljenoj 7/III", "stanujem u [adresa]"),
        ("Pošaljite na Izmišljena 5/II.", "Pošaljite na [adresa]."),
        ("Pošaljite na Izmišljena 5 A, Mostar", "Pošaljite na [adresa], Mostar"),
        ("Pošaljite na Izmišljena 5-7, Mostar", "Pošaljite na [adresa], Mostar"),
        ("Ulica Izmišljena BB", "[adresa]"),
        ("Pošaljite na Izmišljena 12/4/2", "Pošaljite na [adresa]"),
        ("Pošaljite na Izmišljena 24/14a, Mostar", "Pošaljite na [adresa], Mostar"),
        ("Pošaljite na Izmišljena 5/IIa", "Pošaljite na [adresa]"),
        ("Pošaljite na Izmišljena B.B.", "Pošaljite na [adresa]"),
        ("Pošaljite na Izmišljena b.b., Čitluk", "Pošaljite na [adresa], Čitluk"),
        ("Pošaljite na Izmišljena 5B/2", "Pošaljite na [adresa]"),
        ("Pošaljite na Izmišljena 5-12a, Mostar", "Pošaljite na [adresa], Mostar"),
        ("Pošaljite na Izmišljena 12/4-2", "Pošaljite na [adresa]"),
        ("Pošaljite na Izmišljena 7/123", "Pošaljite na [adresa]"),
        ("Pošaljite na Izmišljena 5-7b", "Pošaljite na [adresa]"),
        # After a house number any upper-case letter but X is a building letter, M, L
        # and H too (Tomislav: fail closed). All-caps sizes after a place are therefore
        # over-scrubbed: see test_over_scrubs_all_caps_sizes_after_a_place.
        ("Ulica Izmišljena 5 L", "[adresa]"),
        ("dostava na Izmišljena 3 M sutra", "dostava na [adresa] sutra"),
        ("Ulica Izmišljena 5 L Mostar", "[adresa] Mostar"),
        ("Ulica Izmišljena 5 L, Mostar", "[adresa], Mostar"),
        ("Pošaljite na Izmišljena 12 H", "Pošaljite na [adresa]"),
        ("dostava na Izmišljena 3 M.", "dostava na [adresa]."),
        ("Ulica Izmišljena 5 L\nHvala", "[adresa]\nHvala"),
        ("Pošaljite na Izmišljena 5 \u2013 7", "Pošaljite na [adresa]"),
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
