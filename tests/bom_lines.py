"""Every BOM line the calculators can emit, shared by the price-map and grounding tests."""

import itertools

from martom.bom.common import BomLine, Item, PriceRef
from martom.bom.panel import PANEL_RULES, PANEL_WIDTHS_CM, panel_fence_bom
from martom.bom.roll import ROLL_RULES, RollPostModel, roll_fence_bom


def all_bom_lines() -> list[BomLine]:
    """Every line the panel and roll calculators can emit, over every height and option."""
    lines: list[BomLine] = []
    for height, width in itertools.product(PANEL_RULES, PANEL_WIDTHS_CM):
        lines += panel_fence_bom(sides_m=[10, 10], panel_height_cm=height, panel_width_cm=width)
    synced_options = (frozenset(), frozenset({PriceRef(Item.POST_T, "100 cm")}))
    for height, model, synced in itertools.product(ROLL_RULES, RollPostModel, synced_options):
        bom = roll_fence_bom(
            sides_m=[30, 10],
            roll_height_cm=height,
            post_model=model,
            synced_products=synced,
        )
        lines += bom.lines_with_wire()
    return lines
