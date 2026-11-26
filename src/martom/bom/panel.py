"""Panel fence (panelna ograda) materials list.

Every rule here comes from docs/domain/fence-rules.md, "Panel fence", confirmed by Toni
(Martom owner) on 26 Nov 2026. If this file and fence-rules.md disagree, fence-rules.md
wins. The functions return quantities only; prices are looked up from the database when
the list is shown, so no KM amount lives in this module.
"""

# The customer-facing labels (and the docs quoting them) use the real "×" and "–" signs,
# as in fence-rules.md.
# ruff: noqa: RUF001, RUF002, RUF003

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum


@dataclass(frozen=True)
class PanelHeightRule:
    """What goes with one panel height (fence-rules.md, panel fence table)."""

    post_height_cm: int  # base-plate post (stup s pločicom)
    clips_per_post: int  # PVC clips; a corner post gets twice as many
    # Post without a plate, set in concrete. None = Toni hasn't confirmed the size yet:
    # the line then has no size and no price. Never extrapolate a size here.
    concrete_post_height_cm: int | None


# Panel height in cm -> rule. Sources: docs/domain/fence-rules.md, "Panel fence" table
# (base-plate posts, clips) and "Concrete-in-soil posts" table (Toni, 26 Nov 2026,
# docs PR #50): only the 103 cm panel has a confirmed concrete post (125 cm).
PANEL_RULES: dict[int, PanelHeightRule] = {
    83: PanelHeightRule(post_height_cm=85, clips_per_post=3, concrete_post_height_cm=None),
    103: PanelHeightRule(post_height_cm=105, clips_per_post=3, concrete_post_height_cm=125),
    123: PanelHeightRule(post_height_cm=125, clips_per_post=3, concrete_post_height_cm=None),
    153: PanelHeightRule(post_height_cm=155, clips_per_post=4, concrete_post_height_cm=None),
    203: PanelHeightRule(post_height_cm=205, clips_per_post=5, concrete_post_height_cm=None),
}

# fence-rules.md: "Panel widths: 200 cm and 250 cm". 250 cm is the reference example.
PANEL_WIDTHS_CM: tuple[int, ...] = (200, 250)
DEFAULT_PANEL_WIDTH_CM = 250

# fence-rules.md: "Anchors M8×80: 4 per post, corner posts included."
ANCHORS_PER_POST = 4

# fence-rules.md, Corners: "That corner post gets double clips."
CORNER_CLIP_FACTOR = 2

# fence-rules.md, Concrete-in-soil posts: made to order with a 10–15 day lead time.
MADE_TO_ORDER_LABEL = "po narudžbi, 10–15 dana"

# A closed loop (around a yard) needs at least a triangle.
MIN_CLOSED_SIDES = 3


class PostType(StrEnum):
    BASE_PLATE = "base_plate"  # stock item, the default recommendation
    CONCRETE_IN_SOIL = "concrete_in_soil"  # not stocked: made to order


class Item(StrEnum):
    """What a BOM line is, so the caller can look up its product and price."""

    PANEL = "panel"
    POST_BASE_PLATE = "post_base_plate"
    POST_CONCRETE = "post_concrete"
    PVC_CLIP = "pvc_clip"
    ANCHOR_M8X80 = "anchor_m8x80"
    GATE = "gate"


@dataclass(frozen=True)
class PriceRef:
    """The product whose price a line uses, when it isn't the line's own item and size."""

    item: Item
    size: str


@dataclass(frozen=True)
class BomLine:
    item: Item
    name: str  # Bosnian label shown to the customer
    quantity: int
    size: str | None = None  # e.g. "153 × 250 cm"; None when the size isn't known
    # True: never show a price, show "cijena na upit" (gates, unconfirmed "po narudžbi").
    unit_price_on_request: bool = False
    # True: "po narudžbi"; the order message flags the line so Toni confirms it.
    made_to_order: bool = False
    # Extra status shown with the line, e.g. "po narudžbi, 10–15 dana".
    label: str | None = None
    # Look the price up from this product instead of the line's own item and size.
    # E.g. a 125 cm concrete post costs the same as the 105 cm base-plate post.
    price_from: PriceRef | None = None


def panel_fence_bom(
    *,
    sides_m: Sequence[Decimal | int | float | str],
    panel_height_cm: int,
    panel_width_cm: int = DEFAULT_PANEL_WIDTH_CM,
    closed: bool = False,
    gate: bool = False,
    post_type: PostType | str = PostType.BASE_PLATE,
) -> list[BomLine]:
    """Return the materials list for one panel fence.

    `sides_m` holds the length of each straight side in metres, in order around the
    fence. Neighbouring sides meet at a corner. An open fence has len(sides) - 1 corners;
    a closed loop (`closed=True`, e.g. around a yard) has len(sides) corners.

    Each side is rounded to whole centimetres first, so a float such as
    5.000000000000001 can't add a panel. Each side gets its own whole number of panels
    (a partial panel is cut from a full one), and a side of N panels needs N + 1 posts.
    A corner is one post shared by two sides, so posts = Σ(panels per side + 1) - corners,
    which gives a closed loop as many posts as panels. Corner posts get double clips.

    A gate is a separate "Kapija – cijena na upit" line: the side lengths are taken as
    the fence itself, and no gate opening is subtracted from them.

    Concrete-in-soil posts are made to order and never come with anchors or concrete
    (Martom sells no concrete). Only the confirmed heights get a post size and a price
    reference; the others show "cijena na upit".

    Raises ValueError for an empty or non-list `sides_m`, a side that isn't a positive
    finite number, a closed loop with fewer than 3 sides, a panel height or width Toni
    didn't confirm, or an unknown post type.
    """
    sides_cm = _sides_in_cm(sides_m)
    if not isinstance(closed, bool):
        raise ValueError(f"closed must be True or False, got {closed!r}")
    if closed and len(sides_cm) < MIN_CLOSED_SIDES:
        raise ValueError(
            f"a closed fence needs at least {MIN_CLOSED_SIDES} sides, got {len(sides_cm)}"
        )
    rule = _rule_for(panel_height_cm)
    if not _is_int(panel_width_cm) or panel_width_cm not in PANEL_WIDTHS_CM:
        raise ValueError(f"panel width {panel_width_cm!r} cm is not one of {PANEL_WIDTHS_CM} cm")
    try:
        post_type = PostType(post_type)
    except ValueError:
        choices = [p.value for p in PostType]
        raise ValueError(f"post type {post_type!r} is not one of {choices}") from None

    panels_per_side = [math.ceil(side_cm / panel_width_cm) for side_cm in sides_cm]
    corners = len(sides_cm) if closed else len(sides_cm) - 1
    panels = sum(panels_per_side)
    posts = sum(side_panels + 1 for side_panels in panels_per_side) - corners

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

    if post_type == PostType.BASE_PLATE:
        lines.append(
            BomLine(
                item=Item.POST_BASE_PLATE,
                name=f"Stup {rule.post_height_cm} cm s pločicom",
                quantity=posts,
                size=f"{rule.post_height_cm} cm",
            )
        )
    else:
        lines.append(_concrete_post_line(rule, panel_height_cm, posts))

    lines.append(BomLine(item=Item.PVC_CLIP, name="PVC kopča", quantity=clips))

    # Anchors fix the base plate to concrete; a post set in concrete has no plate to anchor.
    if post_type == PostType.BASE_PLATE:
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


def _concrete_post_line(rule: PanelHeightRule, panel_height_cm: int, posts: int) -> BomLine:
    if rule.concrete_post_height_cm is None:
        # Size and price pending Toni: no size, no price, "cijena na upit".
        return BomLine(
            item=Item.POST_CONCRETE,
            name=f"Stup za betoniranje (za panel {panel_height_cm} cm)",
            quantity=posts,
            unit_price_on_request=True,
            made_to_order=True,
            label=MADE_TO_ORDER_LABEL,
        )
    # Toni: it costs the same as the base-plate post for this panel height, so the line
    # points at that product's price, which comes from the database at call time.
    return BomLine(
        item=Item.POST_CONCRETE,
        name=f"Stup za betoniranje {rule.concrete_post_height_cm} cm",
        quantity=posts,
        size=f"{rule.concrete_post_height_cm} cm",
        made_to_order=True,
        label=MADE_TO_ORDER_LABEL,
        price_from=PriceRef(item=Item.POST_BASE_PLATE, size=f"{rule.post_height_cm} cm"),
    )


def _rule_for(panel_height_cm: int) -> PanelHeightRule:
    rule = PANEL_RULES.get(panel_height_cm) if _is_int(panel_height_cm) else None
    if rule is None:
        raise ValueError(
            f"panel height {panel_height_cm!r} cm is not one of {sorted(PANEL_RULES)} cm"
        )
    return rule


def _is_int(value: object) -> bool:
    # bool is a subclass of int in Python (True == 1), so rule it out explicitly.
    return isinstance(value, int) and not isinstance(value, bool)


def _sides_in_cm(sides_m: Sequence[Decimal | int | float | str]) -> list[Decimal]:
    # A str is a Sequence too ("25" would be the sides "2" and "5"), so rule it out.
    if not isinstance(sides_m, Sequence) or isinstance(sides_m, str | bytes):
        raise ValueError(f"sides must be a list of side lengths in metres, got {sides_m!r}")
    if not sides_m:
        raise ValueError("sides must hold at least one side length")
    return [_side_in_cm(side) for side in sides_m]


def _side_in_cm(side_m: Decimal | int | float | str) -> Decimal:
    if isinstance(side_m, bool) or side_m is None:
        raise ValueError(f"side length must be a number of metres, got {side_m!r}")
    try:
        # str() first: Decimal(0.1) keeps the float's binary error, Decimal("0.1") doesn't.
        metres = Decimal(str(side_m))
    except InvalidOperation:
        raise ValueError(f"side length must be a number of metres, got {side_m!r}") from None
    if not metres.is_finite() or metres <= 0:
        raise ValueError(f"side length must be a positive number of metres, got {side_m!r}")
    side_cm = (metres * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if side_cm <= 0:
        raise ValueError(f"side length must be at least 1 cm, got {side_m!r} m")
    return side_cm
