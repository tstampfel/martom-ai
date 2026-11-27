"""Guardrail and clarify logic: what the customer wants, and what the advisor still needs.

The guardrail asks Haiku for an `Understanding` of the message: in scope or not, what
kind of request, and the fence details *as written* ("oko 150 cm", "25m"). Haiku copies,
Python decides (the same split as the query parser in search/query_parser.py):

- `update_spec` turns those strings, plus the customer's chip answers (`ClarifyAnswers`),
  into a `FenceSpec` with real numbers, and keeps it across turns.
- `plan_turn` decides the route: answer directly (out of scope, chain link, a gate-only
  question, a missing Eurofence height, a fence too large), show clarify chips, or search.

Everything here is plain Python, so every rule from fence-rules.md and the #20 comments is
a unit test, not a prompt instruction the model may forget.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from martom.agent import copy
from martom.api.chat_events import (
    ChipOption,
    ChipQuestion,
    ChipsEvent,
    ExtraLine,
    ProjectFact,
    ProjectSummary,
)
from martom.api.chat_request import ClarifyAnswers
from martom.bom.common import FenceTooLargeError, check_layout, sides_in_cm
from martom.bom.panel import DEFAULT_PANEL_WIDTH_CM, PANEL_RULES, PANEL_WIDTHS_CM
from martom.bom.roll import ROLL_RULES, RollPostModel
from martom.search.query_parser import parse_height_cm

# A height the customer writes is matched to the nearest real one within this many cm
# ("oko 150 cm" -> 153 cm panels), the same tolerance as the search height filter.
HEIGHT_TOLERANCE_CM = 5

RequestKind = Literal[
    "fence_panel",
    "fence_roll",
    "fence_chain_link",
    "fence_eurofence",
    "fence_unknown_type",
    "product",
    "other",
]
Layout = Literal["straight", "around", "unclear", "not_stated"]
FenceKind = Literal["panel", "roll"]


class Understanding(BaseModel):
    """Haiku's reading of one customer message. Strings are copied as written."""

    in_scope: bool = Field(
        description="True when the customer asks about anything a hardware store "
        "(željezarija) like Martom sells or does: fences, posts, mesh, tools, fittings, "
        "screws, chains, building hardware, garden and farm supplies. False for anything "
        "else (weather, politics, homework, other shops)."
    )
    request: RequestKind = Field(
        description="fence_panel: panel fence (panelna ograda, 3D panel). fence_roll: "
        "welded mesh in a roll (varena mreža/ograda u roli). fence_chain_link: chain link "
        "(pletivo, baklava, pletena žica). fence_eurofence: Eurofence. fence_unknown_type: "
        "a fence of no stated type. product: any other product. other: no product at all."
    )
    asks_gate: bool = Field(description="Mentions a gate (kapija, vrata u ogradi).")
    asks_installation: bool = Field(description="Asks about installation (montaža, ugradnja).")
    asks_concrete: bool = Field(
        description="Asks to buy concrete or for construction work (beton, temelj, zidanje)."
    )
    wants_concrete_posts: bool = Field(
        description="Wants posts set in concrete or into the soil (betoniranje stupova, "
        "stupovi u zemlju)."
    )
    height: str | None = Field(description="Fence height exactly as written, e.g. 'oko 150 cm'.")
    lengths: list[str] = Field(
        description="Each length exactly as written, e.g. ['25m'] or ['10 m', '15 m']."
    )
    layout: Layout = Field(
        description="straight: one straight run. around: around a yard, plot or house "
        "(oko dvorišta, okolo). unclear: corners mentioned but not clear. not_stated."
    )
    colour: str | None = Field(description="Colour as written, e.g. 'antracit'.")
    panel_width: str | None = Field(description="Panel width as written, e.g. '200 cm'.")
    post_spacing: str | None = Field(description="Post spacing as written, e.g. '3 m'.")
    search_query: str = Field(
        description="A short Bosnian product search for the catalogue, e.g. "
        "'panelna ograda 153 cm antracit'. No prices, no filler words."
    )


class FenceSpec(BaseModel):
    """What the advisor knows about the customer's fence; kept across turns."""

    kind: FenceKind | None = None
    height_cm: int | None = None  # as the customer asked; snapped to real heights later
    total_length_m: float | None = None  # one length from the message
    sides_m: list[float] = Field(default_factory=list)  # from the layout chips
    closed: bool = False
    layout: Layout = "not_stated"
    posts: str | None = None  # panel: plate/concrete/unknown; roll: t_post/foot_plate
    colour: str | None = None
    panel_width_cm: int | None = None
    requested_spacing_m: float | None = None
    clarified: bool = False  # the chips were shown once already
    invalid_length: bool = False

    def heights(self) -> tuple[int, ...]:
        if self.kind == "panel":
            return tuple(PANEL_RULES)
        if self.kind == "roll":
            return tuple(ROLL_RULES)
        return ()

    def real_height_cm(self) -> int | None:
        """The nearest real height within HEIGHT_TOLERANCE_CM, or None."""
        if self.height_cm is None or not self.heights():
            return None
        nearest = min(self.heights(), key=lambda height: abs(height - self.height_cm))
        return nearest if abs(nearest - self.height_cm) <= HEIGHT_TOLERANCE_CM else None


@dataclass
class Plan:
    route: Literal["clarify", "respond", "retrieve"]
    spec: FenceSpec | None = None
    answer: str | None = None
    chips: ChipsEvent | None = None
    query: str = ""
    notes: list[str] = field(default_factory=list)
    extra_lines: list[ExtraLine] = field(default_factory=list)
    contact_martom: bool = False  # offer the WhatsApp order button with the request as note


# "1.200,5" / "1.200" (thousands dots), "25,5", "25.5", "-3".
_NUMBER = re.compile(r"-?(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)")
_CM = re.compile(r"\d\s*cm\b", re.IGNORECASE)


def parse_length_m(text: str) -> float | None:
    """'25m' -> 25.0, '25,5 m' -> 25.5, '1.200 m' -> 1200.0, '2500 cm' -> 25.0, '-3' -> -3.0.

    A dot followed by groups of exactly three digits is a thousands separator ("1.200 m",
    "1.000,5 m"), as Bosnian writes it; otherwise it is a decimal point ("25.5 m").
    None without a number.

    A zero or negative result is kept on purpose: it is an input error the advisor asks
    about again (Tomislav, 26 Nov 2026), not something to drop silently.
    """
    match = _NUMBER.search(text)
    if not match:
        return None
    text_value = match.group(0)
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", text_value):
        text_value = text_value.replace(".", "")
    value = float(text_value.replace(",", "."))
    return value / 100 if _CM.search(text) else value


def update_spec(
    previous: FenceSpec | None,
    understanding: Understanding,
    answers: ClarifyAnswers | None,
) -> FenceSpec:
    """The fence spec after this message: the previous one, then the message, then chips."""
    spec = (previous or FenceSpec()).model_copy(deep=True)
    if understanding.request == "fence_panel":
        spec.kind = "panel"
    elif understanding.request == "fence_roll":
        spec.kind = "roll"

    height = parse_height_cm(understanding.height)
    if height is not None:
        spec.height_cm = round(height)
    lengths = [parse_length_m(text) for text in understanding.lengths]
    lengths = [length for length in lengths if length is not None]
    if any(length <= 0 for length in lengths):
        spec.invalid_length = True
    elif lengths:
        spec.invalid_length = False
        spec.total_length_m = sum(lengths)
        spec.sides_m = []  # a new length replaces the old layout
    if understanding.layout != "not_stated":
        spec.layout = understanding.layout
    if understanding.colour:
        spec.colour = understanding.colour.strip().lower()
    if understanding.panel_width:
        width = parse_length_m(understanding.panel_width)
        if width is not None:
            # "200 cm" parses as 2.0 m; a bare "200" is centimetres.
            width_cm = round(width * 100) if width < 10 else round(width)
            spec.panel_width_cm = width_cm if width_cm in PANEL_WIDTHS_CM else None
    if understanding.post_spacing:
        spacing = parse_length_m(understanding.post_spacing)
        # A spacing of 0 m or less means nothing; the BOM always uses 2 m anyway, the
        # requested spacing only adds Toni's note (R1-4).
        spec.requested_spacing_m = spacing if spacing is not None and spacing > 0 else None
    if understanding.wants_concrete_posts:
        if spec.kind == "panel":
            spec.posts = "concrete"
        elif spec.kind == "roll":
            spec.posts = RollPostModel.T_POST.value

    if answers is not None:
        _apply_answers(spec, answers)
    return spec


def _apply_answers(spec: FenceSpec, answers: ClarifyAnswers) -> None:
    choices = answers.choices
    if choices.get("fence_type") in ("panel", "roll"):
        spec.kind = choices["fence_type"]  # type: ignore[assignment]
    if "height" in choices and choices["height"].isdigit():
        spec.height_cm = int(choices["height"])
    if "posts" in choices:
        spec.posts = choices["posts"]
    if "color" in choices:
        spec.colour = None if choices["color"] == "any" else choices["color"]
    if answers.sides:
        spec.sides_m = list(answers.sides)
        spec.closed = answers.closed
        spec.invalid_length = False


def plan_turn(
    understanding: Understanding,
    spec: FenceSpec,
    *,
    follow_up: bool = False,
    eurofence_heights: list[int] | None = None,
) -> Plan:
    """Where this turn goes. `follow_up`: the message answers the previous chips."""
    notes = _topic_notes(understanding)
    extra_lines: list[ExtraLine] = []
    if not understanding.in_scope:
        return Plan("respond", answer=copy.OUT_OF_SCOPE)

    request = understanding.request
    fence_follow_up = follow_up and spec.kind is not None
    if request == "fence_chain_link":
        return Plan("respond", answer=_join(copy.CHAIN_LINK, *notes), contact_martom=True)
    if request == "other" and not fence_follow_up:
        answer = _join(*notes) if notes else copy.OUT_OF_SCOPE
        return Plan("respond", answer=answer)
    if request == "fence_eurofence":
        missing = _eurofence_missing(spec.height_cm, eurofence_heights)
        if missing:
            return Plan("respond", answer=_join(missing, *notes))
        return Plan("retrieve", query=understanding.search_query, notes=notes)

    if request in ("fence_panel", "fence_roll", "fence_unknown_type") or fence_follow_up:
        if spec.kind == "panel" and spec.posts == "concrete":
            notes.append(copy.CONCRETE_POSTS)
            extra_lines.append(
                ExtraLine(name=copy.CONCRETE_POSTS_LINE, note=copy.CONCRETE_POSTS_LINE_NOTE)
            )
        return _plan_fence(understanding, spec, notes, extra_lines)

    return Plan("retrieve", query=understanding.search_query, notes=notes)


def _plan_fence(
    understanding: Understanding,
    spec: FenceSpec,
    notes: list[str],
    extra_lines: list[ExtraLine],
) -> Plan:
    spec = spec.model_copy(deep=True)
    if spec.invalid_length:
        spec.clarified = True
        chips = ChipsEvent(intro=copy.INVALID_LENGTH, questions=[_layout_question()])
        return Plan(
            "clarify",
            spec=spec,
            chips=chips,
            answer=copy.INVALID_LENGTH,
            notes=notes,
            extra_lines=extra_lines,
        )

    # A plain straight run defaults to 0 corners without asking (Toni, 26 Nov 2026). After
    # the chips were shown once, a customer who skipped the layout keeps the one length
    # (unless the fence goes around something: then the corners decide the BOM).
    straight = spec.layout == "straight" or (spec.clarified and spec.layout != "around")
    if not spec.sides_m and spec.total_length_m is not None and straight:
        spec.sides_m = [spec.total_length_m]
        spec.closed = False

    required = _required_questions(spec)
    if required:
        optional = _optional_questions(spec, {question.id for question in required})
        spec.clarified = True
        chips = ChipsEvent(
            intro=copy.CLARIFY_INTRO,
            project=_project(spec),
            questions=[*required, *optional],
        )
        return Plan(
            "clarify",
            spec=spec,
            chips=chips,
            answer=copy.CLARIFY_INTRO,
            notes=notes,
            extra_lines=extra_lines,
        )

    try:
        check_layout(sides_in_cm(spec.sides_m), spec.closed)
    except FenceTooLargeError:
        return Plan(
            "respond", spec=spec, answer=_join(copy.FENCE_TOO_LARGE, *notes), contact_martom=True
        )
    except ValueError:
        spec.sides_m = []
        spec.invalid_length = True
        chips = ChipsEvent(intro=copy.INVALID_LENGTH, questions=[_layout_question()])
        return Plan(
            "clarify",
            spec=spec,
            chips=chips,
            answer=copy.INVALID_LENGTH,
            notes=notes,
            extra_lines=extra_lines,
        )

    return Plan(
        "retrieve",
        spec=spec,
        query=understanding.search_query or _fence_query(spec),
        notes=notes,
        extra_lines=extra_lines,
    )


def _required_questions(spec: FenceSpec) -> list[ChipQuestion]:
    questions = []
    if spec.kind is None:
        questions.append(
            _single(
                "fence_type",
                copy.FENCE_TYPE_QUESTION,
                copy.FENCE_TYPE_LABEL,
                [("panel", copy.FENCE_TYPE_PANEL, None), ("roll", copy.FENCE_TYPE_ROLL, None)],
            )
        )
    if not spec.sides_m:
        questions.append(_layout_question())
    if spec.kind is not None and spec.real_height_cm() is None:
        options = [(str(height), f"{height} cm", None) for height in spec.heights()]
        questions.append(_single("height", copy.HEIGHT_QUESTION, copy.HEIGHT_LABEL, options))
    if spec.kind == "roll" and spec.posts not in {model.value for model in RollPostModel}:
        questions.append(_roll_posts_question())
    return questions


def _optional_questions(spec: FenceSpec, asked: set[str]) -> list[ChipQuestion]:
    """Asked only together with a required question, so they never cost an extra turn."""
    questions = []
    if spec.kind == "panel" and spec.posts is None and "posts" not in asked:
        questions.append(_panel_posts_question())
    if spec.kind is not None and spec.colour is None:
        questions.append(
            _single(
                "color",
                copy.COLOUR_QUESTION,
                copy.COLOUR_LABEL,
                [
                    ("antracit", copy.COLOUR_ANTHRACITE, "Antracit"),
                    ("zelena", copy.COLOUR_GREEN, "Zelena"),
                    ("any", copy.COLOUR_ANY, None),
                ],
            )
        )
    return questions


def _layout_question() -> ChipQuestion:
    return ChipQuestion(
        id="layout", text=copy.LAYOUT_QUESTION, label=copy.LAYOUT_LABEL, kind="layout", options=[]
    )


def _panel_posts_question() -> ChipQuestion:
    # Base-plate posts are the default; concrete posts are "po upitu" (fence-rules.md).
    return ChipQuestion(
        id="posts",
        text=copy.POSTS_QUESTION,
        label=copy.POSTS_LABEL,
        kind="single",
        options=[
            ChipOption(value="plate", label=copy.POSTS_PLATE),
            ChipOption(
                value="concrete",
                label=copy.POSTS_CONCRETE,
                short_label=copy.POSTS_CONCRETE_SHORT,
                badge=copy.POSTS_CONCRETE_BADGE,
                note=copy.POSTS_CONCRETE_NOTE,
            ),
            ChipOption(value="unknown", label=copy.POSTS_UNKNOWN),
        ],
        default="plate",
    )


def _roll_posts_question() -> ChipQuestion:
    # fence-rules.md: into soil or concrete -> T-post (a); on a wall or base -> foot plate (b).
    return _single(
        "posts",
        copy.POSTS_QUESTION,
        copy.POSTS_LABEL,
        [
            (RollPostModel.T_POST.value, copy.POSTS_T_POST, copy.POSTS_T_POST_SHORT),
            (RollPostModel.FOOT_PLATE.value, copy.POSTS_FOOT_PLATE, copy.POSTS_FOOT_PLATE_SHORT),
        ],
    )


def _single(
    question_id: str, text: str, label: str, options: Sequence[tuple[str, str, str | None]]
) -> ChipQuestion:
    return ChipQuestion(
        id=question_id,
        text=text,
        label=label,
        kind="single",
        options=[ChipOption(value=v, label=lbl, short_label=short) for v, lbl, short in options],
    )


def _project(spec: FenceSpec) -> ProjectSummary:
    title = {"panel": copy.PROJECT_PANEL, "roll": copy.PROJECT_ROLL}.get(
        spec.kind or "", copy.PROJECT_FENCE
    )
    facts = []
    if spec.height_cm is not None:
        facts.append(ProjectFact(label=copy.HEIGHT_LABEL, value=f"oko {spec.height_cm} cm"))
    return ProjectSummary(title=title, length_m=spec.total_length_m, facts=facts)


def _topic_notes(understanding: Understanding) -> list[str]:
    notes = []
    if understanding.asks_gate:
        notes.append(copy.GATE)
    if understanding.asks_installation:
        notes.append(copy.INSTALLATION)
    if understanding.asks_concrete:
        notes.append(copy.NO_CONCRETE_SALES)
    return notes


def _eurofence_missing(height_cm: int | None, heights: list[int] | None) -> str | None:
    """The "ne postoji" answer for a height the synced Eurofence variants don't have."""
    if height_cm is None or not heights or height_cm in heights:
        return None
    below = [height for height in heights if height < height_cm]
    above = [height for height in heights if height > height_cm]
    offered = [f"{max(below)} cm"] if below else []
    offered += [f"{min(above)} cm"] if above else []
    return copy.EUROFENCE_MISSING_HEIGHT.format(height=height_cm, options=" ili ".join(offered))


def _fence_query(spec: FenceSpec) -> str:
    kind = {"panel": "panelna ograda", "roll": "varena ograda u roli"}.get(spec.kind or "", "")
    height = spec.real_height_cm()
    return f"{kind} {height} cm".strip() if height else kind or "ograda"


def total_length_text(sides_m: list[float]) -> str:
    """'25 m', '25,5 m': for BOM summaries."""
    total = Decimal(str(math.fsum(sides_m))).normalize()
    return f"{format(total, 'f').replace('.', ',')} m"


def default_panel_width(spec: FenceSpec) -> int:
    return spec.panel_width_cm or DEFAULT_PANEL_WIDTH_CM


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part)
