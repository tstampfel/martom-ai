"""`build_search_text`: the compact text search indexes, built in plain Python."""

from martom.extract.schema import ExtractedProduct, ExtractedVariant
from martom.extract.search_text import build_search_text


def variant(**fields: str | None) -> ExtractedVariant:
    defaults: dict[str, str | None] = {
        "label": "x", "height": None, "length": None, "colour": None,
        "size": None, "price": None, "unit": None,
    }  # fmt: skip
    return ExtractedVariant(**{**defaults, **fields})


def product(**fields: object) -> ExtractedProduct:
    defaults: dict[str, object] = {
        "product_type": "stup za ogradu", "brand": None, "material": None,
        "wire_thickness": None, "mesh": None, "colours": [], "uses": [],
        "compatible_with": [], "synonyms": [], "regional_synonyms": [], "variants": [],
    }  # fmt: skip
    return ExtractedProduct.model_validate({**defaults, **fields})


def test_joins_title_type_brand_colours_heights_mesh_uses_compatibility_and_synonyms() -> None:
    text = build_search_text(
        "MREŽA OGRADA ZA KOKE",
        product(
            product_type="mreža za kokoši",
            brand="Fischer",
            colours=["zelena RAL 6005"],
            mesh="25MM",
            uses=["ograđivanje kokošinjca"],
            compatible_with=["T stupovi"],
            synonyms=["koke"],
            variants=[variant(height="H-100CM"), variant(height="H-120CM")],
        ),
    )

    assert text == (
        "MREŽA OGRADA ZA KOKE | mreža za kokoši | Fischer | zelena RAL 6005 | "
        "H-100CM H-120CM | okce 25MM | ograđivanje kokošinjca | T stupovi"
    )  # "koke" and "kokoši" are already in the title and type, so no synonyms part


def test_listing_synonyms_and_their_known_partners_are_added() -> None:
    text = build_search_text(
        "OGRADA H-100", product(product_type="mreža za perad", synonyms=["koke", "stubovi"])
    )

    assert text.endswith("| koke stubovi kokoši stupovi")


def test_adds_known_synonym_pairs_stup_stub() -> None:
    text = build_search_text("STUP METALNI", product(product_type="stup za ogradu"))

    assert text.endswith("| stub")


def test_skips_empty_parts_and_repeated_values() -> None:
    text = build_search_text(
        "Tiplo",
        product(product_type="tiplo", synonyms=["Tiplo"], variants=[variant(height=None)]),
    )

    assert text == "Tiplo"


def test_leaves_out_the_marketing_description_fields() -> None:
    # Material and variant labels stay in the product row, not in the indexed text.
    text = build_search_text(
        "PANEL", product(material="pocinčana čelična žica", variants=[variant(label="dugo")])
    )

    assert "pocinčana" not in text
    assert "dugo" not in text


def test_regional_synonyms_are_appended_with_the_listings_own_synonyms() -> None:
    # #121: the wheelbarrow listing never says "kolica"; Haiku's regional synonyms add it.
    text = build_search_text(
        "KARIOLA KARIOLE TAČKE CIVARE",
        product(
            product_type="kariola",
            synonyms=["kariole"],
            regional_synonyms=["kolica", "Tačke", "građevinska kolica"],
        ),
    )

    # "Tačke" is already a word of the title, so only the new ones are added.
    assert text == "KARIOLA KARIOLE TAČKE CIVARE | kariola | kolica građevinska kolica"


def test_known_partners_of_regional_synonyms_are_added_too() -> None:
    text = build_search_text(
        "T PROFIL 35X35", product(product_type="profil", regional_synonyms=["stup"])
    )

    assert text.endswith("| stup stub")
