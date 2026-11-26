"""Welded roll fence (varena ograda u roli) materials list.

Every rule here comes from docs/domain/fence-rules.md, "Welded roll fence" (Toni, Martom
owner, and Tomislav, 26 Nov 2026), including the suggested tension-wire add-on and the
100 cm support T-posts for short foot-plate fences.
If this file and fence-rules.md disagree, fence-rules.md wins. The functions return
quantities only: prices come from the synced OLX data (or, for products without a
listing, from manual_products.toml) when the list is shown.

Chain link (pletivo, baklava) has different rules, still pending from Toni (M4-2b), so
asking for it raises RulesPendingError instead of applying the welded-roll rules.
"""

# The customer-facing labels use the real "×" and "–" signs, as in fence-rules.md.
# ruff: noqa: RUF001, RUF003

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from martom.bom.common import (
    BomLine,
    Item,
    PriceRef,
    check_layout,
    is_int,
    sides_in_cm,
)


class RollMesh(StrEnum):
    WELDED = "welded"  # varena ograda u roli: the rules below
    CHAIN_LINK = "chain_link"  # pletivo, baklava: rules pending Toni (M4-2b)


class RollPostModel(StrEnum):
    T_POST = "t_post"  # (a) OLX 59473597, into soil or set in concrete
    FOOT_PLATE = "foot_plate"  # (b) OLX 81645615, anchored onto a concrete wall or base


class RulesPendingError(ValueError):
    """Toni hasn't given the rules for this fence yet, so there is no BOM.

    The advisor says the rules are pending and offers to contact Martom.
    """


@dataclass(frozen=True)
class RollHeightRule:
    """What goes with one roll height (fence-rules.md, "Posts: two models")."""

    t_post_height_cm: int  # smallest available T-post >= roll height + 30 cm
    foot_plate_post_height_cm: int  # same as the roll height
    wire_rows: int  # tension wire rows: <= 100 cm -> 2, > 100 cm -> 3


# fence-rules.md, model (a): T-post heights on OLX 59473597.
T_POST_HEIGHTS_CM: tuple[int, ...] = (120, 125, 150, 175, 200)

# Roll height in cm -> rule. Sources: fence-rules.md, "T-post (a) height" table (80 -> 120
# confirmed, the rest derived from Toni's >= roll + 30 cm rule), "Foot-plate post (b)
# height = fence (roll) height" and "Tension wire": <= 100 cm -> 2 rows, > 100 cm -> 3.
ROLL_RULES: dict[int, RollHeightRule] = {
    60: RollHeightRule(t_post_height_cm=120, foot_plate_post_height_cm=60, wire_rows=2),
    80: RollHeightRule(t_post_height_cm=120, foot_plate_post_height_cm=80, wire_rows=2),
    100: RollHeightRule(t_post_height_cm=150, foot_plate_post_height_cm=100, wire_rows=2),
    125: RollHeightRule(t_post_height_cm=175, foot_plate_post_height_cm=125, wire_rows=3),
    150: RollHeightRule(t_post_height_cm=200, foot_plate_post_height_cm=150, wire_rows=3),
}

# fence-rules.md, "Rolls": a roll is 25 m.
ROLL_LENGTH_CM = 2500

# fence-rules.md, "Post spacing and count": the BOM always uses 2 m, even when the
# customer asks for more; a wider request only adds SPACING_NOTE.
POST_SPACING_CM = 200
SPACING_NOTE = "razmak do 3 m ovisno o terenu – potvrdite s Martomom"

# fence-rules.md, "Strainer and corner posts": a strainer every 25 m along a side.
STRAINER_SPACING_CM = 2500

# fence-rules.md, "Bracing": 2 support T-posts per strainer and per corner post.
BRACES_PER_STRAINER = 2
# fence-rules.md, "Bracing" (Toni, 26 Nov 2026 14:29): foot-plate fences at 60, 80 and
# 100 cm are braced with support T-posts 100 cm. That variant of T-post 59473597 isn't on
# OLX yet: until the sync has it, the line shows count and size but no price.
SHORT_BRACE_HEIGHT_CM = 100
SHORT_BRACE_ON_REQUEST_LABEL = "potporni stup 100 cm – cijena na upit"

# fence-rules.md, "Accessories": tensioners per side, 3 per started 25 m.
TENSIONERS_PER_STRETCH = 3
TENSIONER_STRETCH_CM = 2500

# fence-rules.md, "Accessories": tension wire comes in 100 m coils of 2,70 mm wire.
WIRE_COIL_CM = 10000

# fence-rules.md, "Accessories": 3 × M8×70 per foot-plate post.
ANCHORS_PER_FOOT_PLATE_POST = 3


@dataclass(frozen=True)
class RollFenceBom:
    """The materials list plus the suggested tension-wire add-on.

    `lines` is the BOM and its total. `wire_addon` holds the tension wire (and, on
    foot-plate posts, the wire holders), already counted but not part of the BOM or its
    total: the UI offers it with a "Dodaj nosivu žicu" button, which turns it into normal,
    removable lines (`lines_with_wire()`). `spacing_note` is set when the customer asked
    for posts wider than 2 m; the agent and the order message show it.
    """

    lines: tuple[BomLine, ...]
    wire_addon: tuple[BomLine, ...]
    spacing_note: str | None = None

    def lines_with_wire(self) -> tuple[BomLine, ...]:
        """The BOM with the wire add-on accepted: the add-on becomes ordinary lines."""
        return self.lines + self.wire_addon


def roll_fence_bom(
    *,
    sides_m: Sequence[Decimal | int | float | str],
    roll_height_cm: int,
    post_model: RollPostModel | str,
    closed: bool = False,
    requested_spacing_m: Decimal | int | float | str | None = None,
    mesh: RollMesh | str = RollMesh.WELDED,
    synced_prices: Mapping[PriceRef, Decimal] | None = None,
) -> RollFenceBom:
    """Return the materials list for one welded roll fence.

    `sides_m` holds the length of each straight side in metres, in order around the
    fence; neighbouring sides meet at a corner. An open fence has len(sides) - 1 corners,
    a closed loop (`closed=True`) len(sides). Each side is rounded to whole centimetres.

    - Rolls: ceil(total / 25 m).
    - Posts: every 2 m, ceil(side / 2 m) + 1 per side; a corner post is shared, so
      posts = Σ(per side) - corners. Strainers (every 25 m inside a side, and every
      corner) replace line posts, so they don't change the count.
    - Support T-posts: 2 per strainer and per corner post, as tall as the post they brace;
      a 60, 80 or 100 cm foot-plate fence gets 100 cm support T-posts. The two free end
      posts get none (pending Toni).
    - Tensioners: 3 per started 25 m, counted per side.
    - Foot-plate posts: 3 M8x70 anchors each.
    - Tension wire: only in `wire_addon`, never in `lines`.

    `synced_prices` is the caller's price table for synced listings, keyed by the same
    PriceRef a line's `price_from` points at. The BOM only checks which products exist in
    it (the 100 cm T-post variant); it never copies a price into a line. Without it, the
    100 cm brace line is "cijena na upit".

    Raises RulesPendingError for chain link. Raises FenceTooLargeError (a ValueError) for
    a side over 300 m or a total over 1000 m, and ValueError for any other bad input.
    Gates are out of scope: there is no gate input or gate line.
    """
    _check_mesh(mesh)
    sides_cm = sides_in_cm(sides_m)
    corners = check_layout(sides_cm, closed)
    rule = _rule_for(roll_height_cm)
    post_model = _post_model(post_model)
    spacing_note = _spacing_note(requested_spacing_m)

    total_cm = sum(sides_cm, Decimal(0))
    rolls = math.ceil(total_cm / ROLL_LENGTH_CM)
    posts = sum(math.ceil(side / POST_SPACING_CM) + 1 for side in sides_cm) - corners
    # Strainers inside each side; the side's two ends are corners or free ends.
    strainers = sum(math.ceil(side / STRAINER_SPACING_CM) - 1 for side in sides_cm)
    braces = BRACES_PER_STRAINER * (corners + strainers)
    tensioners = sum(
        TENSIONERS_PER_STRETCH * math.ceil(side / TENSIONER_STRETCH_CM) for side in sides_cm
    )

    lines = [
        BomLine(
            item=Item.ROLL_WELDED,
            name=f"Varena ograda u roli {roll_height_cm} cm (25 m)",
            quantity=rolls,
            size=f"{roll_height_cm} cm",
        )
    ]
    if post_model == RollPostModel.T_POST:
        post_height_cm = rule.t_post_height_cm
        lines.append(
            BomLine(
                item=Item.POST_T,
                name=f"T-stup {post_height_cm} cm",
                quantity=posts,
                size=f"{post_height_cm} cm",
            )
        )
    else:
        post_height_cm = rule.foot_plate_post_height_cm
        lines.append(
            BomLine(
                item=Item.POST_FOOT_PLATE,
                name=f"Stup sa stopom {post_height_cm} cm",
                quantity=posts,
                size=f"{post_height_cm} cm",
            )
        )
    if braces:
        lines.append(_brace_line(post_height_cm, braces, synced_prices or {}))
    lines.append(BomLine(item=Item.TENSIONER, name="Zatezač (španer)", quantity=tensioners))
    if post_model == RollPostModel.FOOT_PLATE:
        lines.append(
            BomLine(
                item=Item.ANCHOR_M8X70,
                name="Anker M8×70",
                quantity=posts * ANCHORS_PER_FOOT_PLATE_POST,
                size="M8×70",
            )
        )

    return RollFenceBom(
        lines=tuple(lines),
        wire_addon=_wire_addon(rule, post_model, total_cm, posts),
        spacing_note=spacing_note,
    )


def _brace_line(
    braced_post_height_cm: int, braces: int, synced_prices: Mapping[PriceRef, Decimal]
) -> BomLine:
    if braced_post_height_cm in T_POST_HEIGHTS_CM:
        # Braces are T-posts (OLX 59473597) as tall as the post they brace.
        brace_height_cm = braced_post_height_cm
    else:
        # A 60/80/100 cm foot-plate post: no T-post is that short, so Toni's 100 cm.
        brace_height_cm = SHORT_BRACE_HEIGHT_CM
    size = f"{brace_height_cm} cm"
    price_ref = PriceRef(item=Item.POST_T, size=size)
    if brace_height_cm == SHORT_BRACE_HEIGHT_CM and price_ref not in synced_prices:
        # The 100 cm variant isn't synced yet: count and size, no price.
        return BomLine(
            item=Item.BRACE_T_POST,
            name=f"Potporni stup {size}",
            quantity=braces,
            size=size,
            unit_price_on_request=True,
            label=SHORT_BRACE_ON_REQUEST_LABEL,
        )
    # Priced as that T-post, but a separate line so the customer can remove it.
    return BomLine(
        item=Item.BRACE_T_POST,
        name=f"Potporni T-stup {size}",
        quantity=braces,
        size=size,
        price_from=price_ref,
    )


def _wire_addon(
    rule: RollHeightRule, post_model: RollPostModel, total_cm: Decimal, posts: int
) -> tuple[BomLine, ...]:
    coils = math.ceil(rule.wire_rows * total_cm / WIRE_COIL_CM)
    addon = [
        BomLine(
            item=Item.TENSION_WIRE,
            name="Nosiva žica 2,70 mm (kolut 100 m)",
            quantity=coils,
            size="2,70 mm",
        )
    ]
    # Wire holders only on foot-plate posts; T-post wire is tied through the post's holes.
    # 1 per wire row per post is an assumption pending Toni (fence-rules.md, Still open).
    if post_model == RollPostModel.FOOT_PLATE:
        addon.append(
            BomLine(
                item=Item.WIRE_HOLDER, name="Nosač nosive žice", quantity=rule.wire_rows * posts
            )
        )
    return tuple(addon)


def _check_mesh(mesh: RollMesh | str) -> None:
    try:
        mesh = RollMesh(mesh)
    except ValueError:
        choices = [m.value for m in RollMesh]
        raise ValueError(f"roll mesh {mesh!r} is not one of {choices}") from None
    if mesh == RollMesh.CHAIN_LINK:
        raise RulesPendingError(
            "chain link (pletivo) rules are pending from Toni: no BOM, contact Martom"
        )


def _rule_for(roll_height_cm: int) -> RollHeightRule:
    rule = ROLL_RULES.get(roll_height_cm) if is_int(roll_height_cm) else None
    if rule is None:
        raise ValueError(f"roll height {roll_height_cm!r} cm is not one of {sorted(ROLL_RULES)} cm")
    return rule


def _post_model(post_model: RollPostModel | str) -> RollPostModel:
    try:
        return RollPostModel(post_model)
    except ValueError:
        choices = [m.value for m in RollPostModel]
        raise ValueError(f"post model {post_model!r} is not one of {choices}") from None


def _spacing_note(requested_m: Decimal | int | float | str | None) -> str | None:
    """SPACING_NOTE when the customer asked for posts wider apart than 2 m."""
    if requested_m is None:
        return None
    if isinstance(requested_m, bool):
        raise ValueError(f"requested spacing must be a number of metres, got {requested_m!r}")
    try:
        metres = Decimal(str(requested_m))
    except InvalidOperation:
        raise ValueError(
            f"requested spacing must be a number of metres, got {requested_m!r}"
        ) from None
    if not metres.is_finite() or metres <= 0:
        raise ValueError(f"requested spacing must be a positive number, got {requested_m!r}")
    # Compare in metres: scaling a huge value (9e999999) to cm would overflow Decimal.
    return SPACING_NOTE if metres > Decimal(POST_SPACING_CM) / 100 else None
