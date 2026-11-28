"""The tools the generate step may call: search, product lookup, the fence BOM, the cart.

Each tool is a `ToolSpec`: a name, a description and a pydantic model for its arguments
(sent to Claude as the tool's JSON schema), plus a plain async handler. The graph's own
`tools` node validates the arguments and runs the handler with a `ToolContext`, so a
handler can read the graph state. That is why the fence BOM tool takes no fence arguments:
it reads the lengths, height and posts the customer gave (the `FenceSpec`), so the model
can't pass a length the customer never said.

Every handler returns plain JSON. Prices in it come from the database (product lookup),
from `manual_products.toml` (a manual product, #132) or from the injected `BomPricer`;
listing text inside it is data, never instructions. The grounding check collects every
price, id and quantity from these outputs.

A manual product (#112: something Toni sells without an OLX listing) is identified by its
TOML key (`manual_key`) instead of a listing id. Its name and price come only from the
loaded TOML (`load_manual_products`), never from the `manual_product` search table, whose
price copy exists only for the search price filter; its `url` is always null, because
there is no OLX listing to link to.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

from martom.agent import copy
from martom.agent.catalogue import Catalogue, ProductInfo
from martom.agent.understand import FenceSpec, default_panel_width, total_length_text
from martom.api.chat_events import BomEvent, BomLineOut, ExtraLine, format_price
from martom.bom.common import BomLine, FenceTooLargeError
from martom.bom.manual_products import ManualProduct
from martom.bom.panel import panel_fence_bom
from martom.bom.roll import RollPostModel, roll_fence_bom
from martom.search.retrieve import Retrieval, RetrievedHit

MAX_CART_QUANTITY = 10_000

type Retriever = Callable[[str], Awaitable[Retrieval]]


class BomPricer(Protocol):
    """The unit price of one BOM line from the synced data, or None ("cijena na upit").

    The real one is `martom.agent.pricer.PostgresBomPricer` (#125: the reviewed price map,
    exact match only). Without a pricer every line is unpriced; it never guesses a price.
    """

    async def unit_price(self, line: BomLine) -> Decimal | None: ...


@dataclass
class ToolContext:
    spec: FenceSpec | None
    extra_lines: list[ExtraLine]
    retriever: Retriever
    catalogue: Catalogue
    pricer: BomPricer | None
    # manual_products.toml, loaded once when the graph is built (key -> entry).
    manual_products: Mapping[str, ManualProduct]


class ToolError(Exception):
    """A tool input the model can fix; its message goes back to the model as the result."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Awaitable[dict[str, Any]]]

    def schema(self) -> dict[str, Any]:
        """Anthropic's tool format, which ChatAnthropic.bind_tools accepts as is."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args.model_json_schema(),
        }


# --- search_products ---------------------------------------------------------------


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, description="Short Bosnian product search.")


def hit_json(hit: RetrievedHit) -> dict[str, Any]:
    # A manual product (#112) has no listing id and no link (url None): the model looks it
    # up and adds it to the cart by its manual_key.
    return {
        "source": hit.source,
        "listing_id": hit.listing_id,
        "manual_key": hit.manual_key,
        "title": hit.title,
        "url": hit.url,
        "product_type": hit.product_type,
        "relevance": hit.relevance,
        "matched_filters": hit.matched_filters,
    }


async def _search(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    assert isinstance(args, SearchArgs)
    retrieval = await ctx.retriever(args.query)
    return {"results": [hit_json(hit) for hit in retrieval.hits]}


# --- get_product -------------------------------------------------------------------


class ProductRef(BaseModel):
    """Exactly one of the two: an OLX listing id, or a manual product's key."""

    listing_id: int | None = Field(default=None, ge=1, description="An OLX product.")
    manual_key: str | None = Field(
        default=None,
        min_length=1,
        description="A result with source 'manual' (no OLX listing): its manual_key.",
    )

    @model_validator(mode="after")
    def _one_ref(self) -> "ProductRef":
        if (self.listing_id is None) == (self.manual_key is None):
            raise ValueError("give either listing_id or manual_key")
        return self


class ProductArgs(ProductRef):
    pass


def product_json(product: ProductInfo) -> dict[str, Any]:
    return {
        "source": "olx",
        "listing_id": product.listing_id,
        "manual_key": None,
        "title": product.title,
        "url": product.url,
        "product_type": product.product_type,
        "colours": product.colours,
        "price": _price(product.listing_price),
        "variants": [
            {"label": variant.label, "price": _price(variant.price), "unit": variant.unit}
            for variant in product.variants
        ],
    }


def manual_product_json(product: ManualProduct) -> dict[str, Any]:
    """The TOML entry as get_product returns it: no listing, no link, no variants."""
    return {
        "source": "manual",
        "listing_id": None,
        "manual_key": product.key,
        "title": product.name,
        "url": None,
        "product_type": None,
        "colours": [],
        "price": _manual_price(product),
        "variants": [],
    }


def _manual_product(ctx: ToolContext, key: str) -> ManualProduct:
    product = ctx.manual_products.get(key)
    if product is None:
        raise ToolError(f"no manual product {key!r}")
    return product


async def _get_product(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    assert isinstance(args, ProductArgs)
    if args.manual_key is not None:
        return manual_product_json(_manual_product(ctx, args.manual_key))
    assert args.listing_id is not None  # ProductRef holds exactly one of the two
    product = await ctx.catalogue.get_product(args.listing_id)
    if product is None:
        raise ToolError(f"no active listing {args.listing_id}")
    return product_json(product)


# --- calculate_fence_bom -----------------------------------------------------------


class FenceBomArgs(BaseModel):
    """No fence arguments: the tool uses what the customer told the advisor."""


class FenceTooLargeToolError(ToolError):
    pass


async def _fence_bom(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    spec = ctx.spec
    if spec is None or spec.kind is None or not spec.sides_m:
        raise ToolError("the fence type, height and lengths are not known yet")
    height = spec.real_height_cm()
    if height is None:
        raise ToolError("the fence height is not one Martom sells")
    try:
        if spec.kind == "panel":
            width = default_panel_width(spec)
            lines: Sequence[BomLine] = panel_fence_bom(
                sides_m=spec.sides_m,
                panel_height_cm=height,
                panel_width_cm=width,
                closed=spec.closed,
            )
            addon: Sequence[BomLine] = ()
            notes = [copy.BOM_NOTE]
            summary = f"{total_length_text(spec.sides_m)} · panel {height} cm"
        else:
            if spec.posts not in {model.value for model in RollPostModel}:
                raise ToolError("the post model (T-post or foot plate) is not known yet")
            bom = roll_fence_bom(
                sides_m=spec.sides_m,
                roll_height_cm=height,
                post_model=spec.posts,
                closed=spec.closed,
                requested_spacing_m=spec.requested_spacing_m,
            )
            lines, addon = bom.lines, bom.wire_addon
            notes = [copy.BOM_NOTE] + ([bom.spacing_note] if bom.spacing_note else [])
            posts = "T-stupovi" if spec.posts == RollPostModel.T_POST else "stupovi sa stopom"
            summary = f"{total_length_text(spec.sides_m)} · mreža u roli {height} cm · {posts}"
    except FenceTooLargeError as exc:
        raise FenceTooLargeToolError(copy.FENCE_TOO_LARGE) from exc
    if spec.colour:
        summary += f" · {spec.colour}"

    event = await build_bom_event(
        lines, ctx.pricer, summary=summary, extra_lines=ctx.extra_lines, notes=notes
    )
    result: dict[str, Any] = event.model_dump(mode="json")
    if addon:
        # The suggested "Dodaj nosivu žicu" add-on (fence-rules.md): counted, not in the BOM.
        result["wire_addon"] = [
            line.model_dump(mode="json") for line in await price_lines(addon, ctx.pricer)
        ]
    return result


async def price_lines(lines: Sequence[BomLine], pricer: BomPricer | None) -> list[BomLineOut]:
    priced = []
    for line in lines:
        unit_price = await pricer.unit_price(line) if pricer is not None else None
        priced.append(BomLineOut.from_line(line, unit_price))
    return priced


async def build_bom_event(
    lines: Sequence[BomLine],
    pricer: BomPricer | None,
    *,
    summary: str,
    extra_lines: list[ExtraLine],
    notes: list[str],
) -> BomEvent:
    """The bom event: the pricer's prices, the total of the priced lines, nothing guessed."""
    out = await price_lines(lines, pricer)
    totals = [Decimal(line.line_total) for line in out if line.line_total is not None]
    return BomEvent(
        title=copy.BOM_TITLE,
        summary=summary,
        lines=out,
        total=format_price(sum(totals, Decimal(0))) if totals else None,
        unpriced_count=sum(1 for line in out if line.price_on_request),
        extra_lines=extra_lines,
        notes=notes,
    )


# --- add_to_cart -------------------------------------------------------------------


class CartArgs(ProductRef):
    variant_label: str | None = Field(
        default=None, description="Exactly as get_product returned it; null without variants."
    )
    quantity: int = Field(ge=1, le=MAX_CART_QUANTITY)


async def _add_to_cart(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    assert isinstance(args, CartArgs)
    if args.manual_key is not None:
        manual = _manual_product(ctx, args.manual_key)
        if args.variant_label is not None:
            raise ToolError(f"manual product {args.manual_key!r} has no variants")
        return {
            "cart_line": {
                "source": "manual",
                "listing_id": None,
                "manual_key": manual.key,
                "title": manual.name,
                "url": None,
                "variant_label": None,
                "quantity": args.quantity,
                "unit_price": _manual_price(manual),
            }
        }
    assert args.listing_id is not None  # ProductRef holds exactly one of the two
    product = await ctx.catalogue.get_product(args.listing_id)
    if product is None:
        raise ToolError(f"no active listing {args.listing_id}")
    if args.variant_label is None:
        if product.variants:
            labels = [variant.label for variant in product.variants]
            raise ToolError(f"pick one of the variants: {labels}")
        unit_price = product.listing_price
    else:
        match = next((v for v in product.variants if v.label == args.variant_label), None)
        if match is None:
            raise ToolError(f"listing {args.listing_id} has no variant {args.variant_label!r}")
        unit_price = match.price
    return {
        "cart_line": {
            "source": "olx",
            "listing_id": product.listing_id,
            "manual_key": None,
            "title": product.title,
            "url": product.url,
            "variant_label": args.variant_label,
            "quantity": args.quantity,
            "unit_price": _price(unit_price),
        }
    }


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "search_products",
        "Search Martom's active catalogue. Returns listing ids, titles and links, no prices. "
        "A result with source 'manual' has no listing id and no link, only a manual_key.",
        SearchArgs,
        _search,
    ),
    ToolSpec(
        "get_product",
        "One product with its variants and prices: an active listing by listing_id, or a "
        "manual product (source 'manual') by manual_key.",
        ProductArgs,
        _get_product,
    ),
    ToolSpec(
        "calculate_fence_bom",
        "The materials list for the customer's fence, from the lengths, height and posts "
        "they gave. Use it for every quantity and total of a fence.",
        FenceBomArgs,
        _fence_bom,
    ),
    ToolSpec(
        "add_to_cart",
        "Add one product variant to the customer's cart when they ask for it: by listing_id, "
        "or by manual_key for a manual product (which has no variants).",
        CartArgs,
        _add_to_cart,
    ),
)
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def _price(amount: Decimal | None) -> str | None:
    return format_price(amount) if amount is not None and amount.is_finite() else None


def _manual_price(product: ManualProduct) -> str | None:
    # 0 KM in the TOML is not a price (the BOM pricer's rule too): "cijena na upit".
    return _price(product.price_km) if product.price_km > 0 else None
