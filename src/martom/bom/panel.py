"""Panel fence (panelna ograda) materials list.

Every rule here comes from docs/domain/fence-rules.md, "Panel fence", confirmed by Toni
(Martom owner) on 26 Nov 2026. If this file and fence-rules.md disagree, fence-rules.md
wins. The functions return quantities only; prices are looked up from the database when
the list is shown, so no KM amount lives in this module.
"""

# The customer-facing labels use the real "×" and "–" signs, as in fence-rules.md.
# ruff: noqa: RUF001, RUF003

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


@dataclass(frozen=True)
class PanelHeightRule:
    """What goes with one panel height (fence-rules.md, panel fence table)."""

    post_height_cm: int  # base-plate post (stup s pločicom)
    clips_per_post: int  # PVC clips; a corner post gets twice as many


# Panel height in cm -> rule. Source: docs/domain/fence-rules.md, "Panel fence" table.
PANEL_RULES: dict[int, PanelHeightRule] = {
    83: PanelHeightRule(post_height_cm=85, clips_per_post=3),
    103: PanelHeightRule(post_height_cm=105, clips_per_post=3),
    123: PanelHeightRule(post_height_cm=125, clips_per_post=3),
    153: PanelHeightRule(post_height_cm=155, clips_per_post=4),
    203: PanelHeightRule(post_height_cm=205, clips_per_post=5),
}

# fence-rules.md: "Panel widths: 200 cm and 250 cm". 250 cm is the reference example.
PANEL_WIDTHS_CM: tuple[int, ...] = (200, 250)
DEFAULT_PANEL_WIDTH_CM = 250

# fence-rules.md: "Anchors M8×80: 4 per post, corner posts included."
ANCHORS_PER_POST = 4

# fence-rules.md, Corners: "That corner post gets double clips."
CORNER_CLIP_FACTOR = 2


class PostType(StrEnum):
    BASE_PLATE = "base_plate"  # stock item, the default recommendation
    CONCRETE_IN_SOIL = "concrete_in_soil"  # not stocked: "po narudžbi", price on request


class Item(StrEnum):
    """What a BOM line is, so the caller can look up its product and price."""

    PANEL = "panel"
    POST_BASE_PLATE = "post_base_plate"
    POST_CONCRETE = "post_concrete"
    PVC_CLIP = "pvc_clip"
    ANCHOR_M8X80 = "anchor_m8x80"
    GATE = "gate"


@dataclass(frozen=True)
class BomLine:
    item: Item
    name: str  # Bosnian label shown to the customer
    quantity: int
    size: str | None = None  # e.g. "153 × 250 cm"; None when the size isn't known
    # True: never show a price, show "cijena na upit" (gates, "po narudžbi" items).
    unit_price_on_request: bool = False
    # True: "po narudžbi"; the order message flags the line so Toni confirms it.
    made_to_order: bool = False


def panel_fence_bom(
    *,
    length_m: Decimal | int | float,
    panel_height_cm: int,
    panel_width_cm: int = DEFAULT_PANEL_WIDTH_CM,
    corners: int = 0,
    gate: bool = False,
    post_type: PostType = PostType.BASE_PLATE,
) -> list[BomLine]:
    """Return the materials list for one panel fence.

    `length_m` is the total fence length. It is rounded to whole centimetres first, so a
    float such as 5.000000000000001 can't add a panel. Every started panel counts as a
    whole panel (a partial panel is cut from a full one), and a run of N panels needs
    N + 1 posts. A corner is one of those posts, shared by the two sides, so corners add
    no posts; each corner post gets double clips. Panels are counted from the total
    length, because the inputs don't include the length of each side.

    Raises ValueError for a length that isn't positive, a panel height or width Toni
    didn't confirm, or more corners than there are inner posts.
    """
    length_cm = _length_in_cm(length_m)
    if length_cm <= 0:
        raise ValueError(f"length must be positive, got {length_m} m")
    rule = PANEL_RULES.get(panel_height_cm)
    if rule is None:
        raise ValueError(
            f"panel height {panel_height_cm} cm is not one of {sorted(PANEL_RULES)} cm"
        )
    if panel_width_cm not in PANEL_WIDTHS_CM:
        raise ValueError(f"panel width {panel_width_cm} cm is not one of {PANEL_WIDTHS_CM} cm")

    panels = math.ceil(length_cm / panel_width_cm)
    posts = panels + 1
    inner_posts = posts - 2  # the two end posts can't be corners
    if corners < 0:
        raise ValueError(f"corners can't be negative, got {corners}")
    if corners > inner_posts:
        raise ValueError(
            f"{corners} corner(s) don't fit: {panels} panel(s) have only "
            f"{inner_posts} inner post(s) where a corner can be"
        )

    # A corner post holds two panel edges at an angle, so its clips are doubled.
    clips = posts * rule.clips_per_post + corners * rule.clips_per_post * (CORNER_CLIP_FACTOR - 1)

    lines = [
        BomLine(
            item=Item.PANEL,
            name=f"Panel {panel_height_cm} × {panel_width_cm} cm",
            quantity=panels,
            size=f"{panel_height_cm} × {panel_width_cm} cm",
        ),
    ]

    if post_type is PostType.BASE_PLATE:
        lines.append(
            BomLine(
                item=Item.POST_BASE_PLATE,
                name=f"Stup {rule.post_height_cm} cm s pločicom",
                quantity=posts,
                size=f"{rule.post_height_cm} cm",
            )
        )
    else:
        # Martom doesn't stock these and Toni gave no length for them, so no size.
        lines.append(
            BomLine(
                item=Item.POST_CONCRETE,
                name=f"Stup za betoniranje (za panel {panel_height_cm} cm) – po narudžbi",
                quantity=posts,
                unit_price_on_request=True,
                made_to_order=True,
            )
        )

    lines.append(BomLine(item=Item.PVC_CLIP, name="PVC kopča", quantity=clips))

    # Anchors fix the base plate to concrete; a post set in concrete has no plate to anchor.
    if post_type is PostType.BASE_PLATE:
        lines.append(
            BomLine(
                item=Item.ANCHOR_M8X80,
                name="Anker M8×80",
                quantity=posts * ANCHORS_PER_POST,
                size="M8×80",
            )
        )

    if gate:
        # fence-rules.md, Gates: a separate product; never calculate a gate price.
        lines.append(
            BomLine(
                item=Item.GATE,
                name="Kapija – cijena na upit",
                quantity=1,
                unit_price_on_request=True,
            )
        )

    return lines


def _length_in_cm(length_m: Decimal | int | float) -> Decimal:
    # str() first: Decimal(0.1) would keep the float's binary error, Decimal("0.1") doesn't.
    metres = Decimal(str(length_m))
    return (metres * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
