"""What Claude Haiku returns for one listing (structured output).

These Pydantic models are the contract with the model: LangChain turns them into a JSON
schema, the API constrains the reply to it, and Pydantic validates the reply (like a Zod
schema used both for the tool definition and for parsing). A reply that doesn't validate
raises, and the extractor retries.

Every string is copied from the listing **exactly as written**, odd sizes, typos and
upper case included. The one exception is `regional_synonyms`, which the model adds from
its own knowledge, for search only (#121). Nothing here converts units or fixes values;
prices are parsed into numbers later, in plain Python (`martom.extract.prices`).

`Field(description=...)` text goes into the JSON schema the model sees, so it is part of
the prompt. Keep it short and concrete.
"""

from pydantic import BaseModel, ConfigDict, Field


class ExtractedVariant(BaseModel):
    """One sellable variant: one price line or phrase of the listing."""

    # extra="forbid": an unexpected key in the reply is an error, not silently dropped.
    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description="The variant's line or phrase copied verbatim from the listing, "
        "without emoji, e.g. '3D Panelna ograda 83 x 200 cm (Ø 4,20/4,40 mm PVC)'."
    )
    height: str | None = Field(
        description="Height exactly as written (e.g. '83', 'H-100CM', '100CM'), or null."
    )
    length: str | None = Field(
        description="Length or width exactly as written (e.g. '200 cm', '20METARA', "
        "'ROLA 50M', '100 m'), or null."
    )
    colour: str | None = Field(description="Colour of this variant as written, or null.")
    size: str | None = Field(
        description="Any other size that tells the variants apart, as written "
        "(e.g. 'SX8', '50MM', '43X50X59CM', '100/8/15'), or null."
    )
    price: str | None = Field(
        description="This variant's price copied exactly as written, with its currency "
        "(e.g. '44,00 KM', '88,00KM'). Null if the listing gives no price for it."
    )
    unit: str | None = Field(
        description="What the price is for, as written (e.g. 'panel', 'kom', 'ROLA 50M'), or null."
    )


class ExtractedProduct(BaseModel):
    """The structured product for one listing."""

    model_config = ConfigDict(extra="forbid")

    product_type: str = Field(
        description="What the product is, in Bosnian, lower case, short "
        "(e.g. 'panelna ograda', 'stup za ogradu', 'kutna brusilica', 'tiplo')."
    )
    brand: str | None = Field(description="Brand or maker named in the listing, or null.")
    material: str | None = Field(
        description="Material and finish as the listing states them, or null."
    )
    wire_thickness: str | None = Field(
        description="Wire diameter or wall thickness as written (e.g. 'Ø 4,20/4,40 mm'), or null."
    )
    mesh: str | None = Field(
        description="Mesh opening as written (e.g. '25MM', '75x50mm'), or null."
    )
    colours: list[str] = Field(description="Colours offered, as written (RAL codes kept).")
    uses: list[str] = Field(
        description="What the listing says it is for, as short phrases in its own words."
    )
    compatible_with: list[str] = Field(
        description="Other products the listing says it goes with (posts, clips, "
        "anchors, mesh types), in its own words."
    )
    synonyms: list[str] = Field(
        description="Other names the listing itself uses for this product "
        "(e.g. 'stub', 'stupovi', 'koke', 'fleksa'). Only words that appear in it."
    )
    # The one field that is NOT copied from the listing (#121): common names customers use
    # for this kind of product, so search finds a "KARIOLA TAČKE" listing for "kolica".
    # Python appends them to search_text only; customers never see them.
    regional_synonyms: list[str] = Field(
        description="Other common names that customers in Bosnia and Herzegovina and "
        "Croatia use for this kind of product, even if the listing doesn't use them, "
        "lower case (e.g. for a wheelbarrow: 'kolica', 'tačke', 'kariola', 'civare'; "
        "for a wire tensioner: 'španer', 'zatezač'). At most 6. Empty if none are common."
    )
    variants: list[ExtractedVariant] = Field(
        description="Every variant with its own size, colour or price, in listing order. "
        "Empty if the listing sells one thing without variants."
    )
