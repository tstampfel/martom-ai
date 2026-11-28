"""Fake models, catalogue, pricer and retriever for the agent graph tests: no network."""

# ruff: noqa: RUF001

import itertools
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage

from martom.agent.catalogue import ProductInfo, VariantInfo
from martom.agent.graph import EUROFENCE_LISTING_ID
from martom.agent.understand import Understanding
from martom.bom.common import BomLine, Item
from martom.search.keyword import SearchFilters
from martom.search.retrieve import Retrieval, RetrievedHit

PANEL_ID = 63996799
PANEL = ProductInfo(
    listing_id=PANEL_ID,
    title="PANEL 3D OGRADA",
    slug="panel-3d-ograda-63996799",
    product_type="panelna ograda",
    colours=["antracit", "zelena"],
    listing_price=None,
    variants=[
        VariantInfo("153 × 250", Decimal("53.00"), "panel", "153"),
        VariantInfo("103 × 250", Decimal("38.00"), "panel", "103"),
    ],
)
EUROFENCE = ProductInfo(
    listing_id=EUROFENCE_LISTING_ID,
    title="EUROFENCE",
    slug="eurofence-48752061",
    product_type="ograda u roli",
    colours=[],
    listing_price=None,
    variants=[VariantInfo(f"{h} cm", None, "rola", str(h)) for h in (60, 80, 125)],
)
# The one entry of the real manual_products.toml (Toni, 26 Nov: 0.80 KM).
ANCHOR_KEY = "anchor_m8x70"
ANCHOR_NAME = "Anker M8×70"
PRICES_26_NOV = {
    Item.PANEL: Decimal("53.00"),
    Item.POST_BASE_PLATE: Decimal("24.90"),
    Item.PVC_CLIP: Decimal("1.00"),
    Item.ANCHOR_M8X80: Decimal("1.30"),
}


class FakeChat(GenericFakeChatModel):
    """Replays scripted replies; binding tools changes nothing."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeChat":  # type: ignore[override]
        return self


class FakeCatalogue:
    def __init__(self, *products: ProductInfo) -> None:
        self.products = {product.listing_id: product for product in products}

    async def get_product(self, listing_id: int) -> ProductInfo | None:
        return self.products.get(listing_id)

    async def variant_heights(self, listing_id: int) -> list[int]:
        product = self.products.get(listing_id)
        return [int(v.height) for v in product.variants if v.height] if product else []


class FakePricer:
    async def unit_price(self, line: BomLine) -> Decimal | None:
        return PRICES_26_NOV.get(line.item)


class FakeRetriever:
    def __init__(
        self, relevance: float | None = 0.75, *, manual: bool = False, listings: bool = True
    ) -> None:
        self.queries: list[str] = []
        self.relevance = relevance
        self.manual = manual  # add the anchor from manual_products.toml (#112)
        self.listings = listings  # False: the manual hit alone

    async def __call__(self, query: str) -> Retrieval:
        self.queries.append(query)
        hit = RetrievedHit(
            listing_id=PANEL_ID,
            title=PANEL.title,
            slug=PANEL.slug,
            product_type="panelna ograda",
            rrf_score=0.03,
            relevance=self.relevance,
            keyword_rank=1,
            vector_rank=1,
        )
        hits = [hit] if self.listings else []
        if self.manual:
            hits.append(
                RetrievedHit(
                    listing_id=None,
                    title=ANCHOR_NAME,
                    slug=None,
                    product_type=ANCHOR_NAME,  # a TOML entry has no type; the name stands in
                    rrf_score=0.02,
                    relevance=self.relevance,
                    keyword_rank=2,
                    vector_rank=None,
                    source="manual",
                    manual_key=ANCHOR_KEY,
                )
            )
        return Retrieval(hits, SearchFilters(), True, False, 0, self.relevance is not None)


def understanding(**fields: Any) -> Understanding:
    base: dict[str, Any] = {
        "in_scope": True,
        "request": "fence_panel",
        "asks_gate": False,
        "asks_installation": False,
        "asks_concrete": False,
        "wants_concrete_posts": False,
        "height": None,
        "lengths": [],
        "layout": "not_stated",
        "colour": None,
        "panel_width": None,
        "post_spacing": None,
        "search_query": "panelna ograda 153 cm",
    }
    return Understanding(**(base | fields))


class Recorder:
    """A fake structured-output model that returns scripted values and keeps its inputs."""

    def __init__(self, *values: Any) -> None:
        self.values = itertools.cycle(values) if values else None
        self.inputs: list[Sequence[BaseMessage]] = []

    def __call__(self, messages: Sequence[BaseMessage]) -> Any:
        self.inputs.append(messages)
        if self.values is None:
            raise RuntimeError("model down")
        return next(self.values)


def tool_call(name: str, args: dict[str, Any] | None = None) -> AIMessage:
    call_id = f"call-{uuid.uuid4().hex[:8]}"
    return AIMessage("", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])
