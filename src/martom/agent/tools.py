"""The tools the generate step may call: search, product lookup, the fence BOM, the cart.

Each tool is a `ToolSpec`: a name, a description and a pydantic model for its arguments
(sent to Claude as the tool's JSON schema), plus a plain async handler. The graph's own
`tools` node validates the arguments and runs the handler with a `ToolContext`, so a
handler can read the graph state. That is why the fence BOM tool takes no fence arguments:
it reads the lengths, height and posts the customer gave (the `FenceSpec`), so the model
can't pass a length the customer never said.

Every handler returns plain JSON. Prices in it come from the database (product lookup) or
from the injected `BomPricer`; listing text inside it is data, never instructions. The
grounding check collects every price, id and quantity from these outputs.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field

from martom.agent import copy
from martom.agent.catalogue import Catalogue, ProductInfo
from martom.agent.understand import FenceSpec, default_panel_width, total_length_text
from martom.api.chat_events import BomEvent, BomLineOut, ExtraLine, format_price
from martom.bom.common import BomLine, FenceTooLargeError
from martom.bom.panel import panel_fence_bom
from martom.bom.roll import RollPostModel, roll_fence_bom
from martom.search.retrieve import Retrieval, RetrievedHit

MAX_CART_QUANTITY = 10_000

type Retriever = Callable[[str], Awaitable[Retrieval]]


class BomPricer(Protocol):
    """The unit price of one BOM line from the synced data, or None ("cijena na upit").

    #125 adds the real one. Until then the graph runs without a pricer and every line is
    unpriced; it never guesses a price.
    """

    async def unit_price(self, line: BomLine) -> Decimal | None: ...


@dataclass
class ToolContext:
    spec: FenceSpec | None
    extra_lines: list[ExtraLine]
    retriever: Retriever
    catalogue: Catalogue
    pricer: BomPricer | None


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
    # A manual product (#112) has no listing id or link; its full handling is #132.
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


class ProductArgs(BaseModel):
    listing_id: int = Field(ge=1)


def product_json(product: ProductInfo) -> dict[str, Any]:
    return {
        "listing_id": product.listing_id,
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


async def _get_product(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    assert isinstance(args, ProductArgs)
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


class CartArgs(BaseModel):
    listing_id: int = Field(ge=1)
    variant_label: str | None = Field(
        default=None, description="Exactly as get_product returned it; null without variants."
    )
    quantity: int = Field(ge=1, le=MAX_CART_QUANTITY)


async def _add_to_cart(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    assert isinstance(args, CartArgs)
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
            "listing_id": product.listing_id,
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
        "Search Martom's active catalogue. Returns listing ids, titles and links, no prices.",
        SearchArgs,
        _search,
    ),
    ToolSpec(
        "get_product",
        "One active listing with its variants and their prices from the database.",
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
        "Add one product variant to the customer's cart when they ask for it.",
        CartArgs,
        _add_to_cart,
    ),
)
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def _price(amount: Decimal | None) -> str | None:
    return format_price(amount) if amount is not None and amount.is_finite() else None
