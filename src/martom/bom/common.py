"""Types and input checks shared by every fence BOM (panel, welded roll).

The rules come from docs/domain/fence-rules.md. Nothing here knows a price: a line names
its product (item and size) and the price is looked up when the list is shown.
"""

# Comments quote labels with the real "×" and "–" signs, as in fence-rules.md.
# ruff: noqa: RUF003

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

# A closed loop (around a yard) needs at least a triangle.
MIN_CLOSED_SIDES = 3

# Length limits (Tomislav, 26 Nov 2026), both inclusive. A bigger fence gets no BOM: the
# advisor sends the customer to Martom directly (M4-3 maps FenceTooLargeError to that).
MAX_SIDE_M = 300
MAX_TOTAL_M = 1000


class FenceLimit(StrEnum):
    SIDE = "side"  # one side is longer than MAX_SIDE_M
    TOTAL = "total"  # all sides together are longer than MAX_TOTAL_M


class FenceTooLargeError(ValueError):
    """The fence is valid but too large for a calculated BOM.

    A subclass of ValueError, so code that only catches ValueError still gets no BOM.
    `limit` says which limit was hit, `value_m` the length that broke it (metres, after
    rounding to whole centimetres) and `max_m` the limit itself.
    """

    def __init__(self, limit: FenceLimit, value_m: Decimal, max_m: int) -> None:
        self.limit = limit
        self.value_m = value_m
        self.max_m = max_m
        super().__init__(f"fence {limit} of {value_m} m is over the {max_m} m limit")


class Item(StrEnum):
    """What a BOM line is, so the caller can look up its product and price."""

    # Panel fence
    PANEL = "panel"
    POST_BASE_PLATE = "post_base_plate"
    POST_CONCRETE = "post_concrete"
    PVC_CLIP = "pvc_clip"
    ANCHOR_M8X80 = "anchor_m8x80"
    # Welded roll fence
    ROLL_WELDED = "roll_welded"  # OLX listing not recorded in fence-rules.md yet
    POST_T = "post_t"  # T-post, OLX 59473597
    POST_FOOT_PLATE = "post_foot_plate"  # stup sa stopom, OLX 81645615
    BRACE_T_POST = "brace_t_post"  # support T-post, same listing as POST_T
    TENSIONER = "tensioner"  # španer, OLX 18129143
    TENSION_WIRE = "tension_wire"  # 100 m coil, OLX 46677177
    WIRE_HOLDER = "wire_holder"  # OLX 48475613
    ANCHOR_M8X70 = "anchor_m8x70"  # no OLX listing: manual_products.toml


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
    # True: never show a price, show "cijena na upit" (unconfirmed items).
    unit_price_on_request: bool = False
    # True: "po narudžbi"; the order message flags the line so Toni confirms it.
    made_to_order: bool = False
    # Extra status shown with the line, e.g. "po narudžbi, 10–15 dana".
    label: str | None = None
    # Look the price up from this product instead of the line's own item and size.
    # E.g. a 125 cm concrete post costs the same as the 105 cm base-plate post.
    price_from: PriceRef | None = None


def is_int(value: object) -> bool:
    # bool is a subclass of int in Python (True == 1), so rule it out explicitly.
    return isinstance(value, int) and not isinstance(value, bool)


def check_layout(sides_cm: Sequence[Decimal], closed: bool) -> int:
    """Check the total length and `closed`; return the number of corners.

    An open fence has len(sides) - 1 corners, a closed loop len(sides).
    """
    total_m = sum(sides_cm, Decimal(0)) / 100
    if total_m > MAX_TOTAL_M:
        raise FenceTooLargeError(FenceLimit.TOTAL, total_m, MAX_TOTAL_M)
    if not isinstance(closed, bool):
        raise ValueError(f"closed must be True or False, got {closed!r}")
    if closed and len(sides_cm) < MIN_CLOSED_SIDES:
        raise ValueError(
            f"a closed fence needs at least {MIN_CLOSED_SIDES} sides, got {len(sides_cm)}"
        )
    return len(sides_cm) if closed else len(sides_cm) - 1


def sides_in_cm(sides_m: Sequence[Decimal | int | float | str]) -> list[Decimal]:
    """Each side length in whole centimetres, checked against MAX_SIDE_M."""
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
    try:
        # Whole centimetres, so float noise like 5.000000000000001 m can't add a panel.
        side_cm = (metres * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    except InvalidOperation:  # more digits than Decimal's precision, e.g. 1e27 m
        # Round to whole metres instead: that can't overflow, and is whole cm too.
        whole_m = metres.to_integral_value(rounding=ROUND_HALF_UP)
        raise FenceTooLargeError(FenceLimit.SIDE, whole_m, MAX_SIDE_M) from None
    if side_cm <= 0:
        raise ValueError(f"side length must be at least 1 cm, got {side_m!r} m")
    if side_cm > MAX_SIDE_M * 100:
        raise FenceTooLargeError(FenceLimit.SIDE, side_cm / 100, MAX_SIDE_M)
    return side_cm
