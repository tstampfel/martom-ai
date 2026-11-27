"""The advisor's LangGraph graph (docs/PLAN.md, "Agent graph").

    guardrail ──(clarify / refuse)──> clarify ──> END
        │
        └──> retrieve ──> grade ──(weak, < 2 rewrites)──> rewrite ──> retrieve
                            │
                            └──> generate <──> tools
                                    │
                                    └──> grounding ──(fails once)──> generate
                                            └──> END (answer, or plain search results)

A LangGraph graph is a state machine: every node is an async function that gets the
current state (a dict) and returns only the keys it changes; edges pick the next node.
Like a Redux reducer per step, except `messages` has a reducer (`add_messages`) that
appends instead of replacing.

What each node does:

- guardrail: Haiku reads the message (`Understanding`); Python (agent/understand.py)
  keeps the fence spec across turns and plans the turn.
- clarify: the chips, or the direct answer (out of scope, gate, chain link, fence too
  large, Eurofence height that doesn't exist).
- retrieve: search.retrieve (filters, hybrid search, rerank) through the injected
  `Retriever`.
- grade: no hits, or a best rerank relevance under RELEVANCE_CUTOFF, is weak without a
  model call; otherwise Haiku judges the fit.
- rewrite: Haiku writes a better query; at most MAX_REWRITES times per turn.
- generate: Sonnet answers and may call the tools (agent/tools.py).
- tools: runs the tool calls and records every price, id and quantity they returned.
- grounding: agent/grounding.py; a failed answer is removed and generate retries once
  with the problems listed; a second failure answers with plain search results.

Every node and model call is a LangSmith run when LANGSMITH_TRACING is on: LangGraph
traces each node under its name, and each model call carries a run name and tags.

The checkpointer is injected (`build_graph(..., checkpointer=...)`): tests use
InMemorySaver, the API opens the graph with the Postgres one through
`agent.factory.open_agent_graph` (#124). With a checkpointer, each
conversation is a `thread_id` and the fence spec and messages survive between requests.
Output for the SSE endpoint (#21): `answer`, `chips`, `bom` (send it when `bom_changed`),
`cart` (this turn's additions), `hits`, `contact_martom` (offer the WhatsApp button with
the customer's last message as the note) and `fallback` in the final state.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from martom.agent import copy
from martom.agent.catalogue import Catalogue
from martom.agent.grounding import Facts, check_grounding, facts_from_output, numbers_in
from martom.agent.prompts import (
    GENERATE_SYSTEM,
    GRADE_SYSTEM,
    GUARD_SYSTEM,
    PROMPT_VERSION,
    REWRITE_SYSTEM,
    Grade,
    Rewrite,
    tagged,
)
from martom.agent.tools import (
    TOOLS,
    TOOLS_BY_NAME,
    BomPricer,
    FenceTooLargeToolError,
    Retriever,
    ToolContext,
    ToolError,
    hit_json,
)
from martom.agent.understand import (
    FenceSpec,
    Understanding,
    default_panel_width,
    plan_turn,
    update_spec,
)
from martom.api.chat_events import ExtraLine
from martom.api.chat_request import ClarifyAnswers
from martom.bom.roll import POST_SPACING_CM

logger = logging.getLogger(__name__)

GRAPH_NAME = "martom_agent"
MAX_REWRITES = 2
MAX_GROUNDING_RETRIES = 1
# Tool rounds per answer; more means the model is looping, so the answer falls back.
MAX_TOOL_ROUNDS = 6
# fence-rules.md, "Eurofence": OLX listing 48752061.
EUROFENCE_LISTING_ID = 48752061
# "Nothing relevant" floor for the best rerank score, tuned on rerank-v5.0-fast (#16,
# evals/baselines.md): on-topic best matches scored 0.71-0.79, the weakest real request
# ("kolica") 0.45, the off-topic question 0.004. 0.1 sits far below every real request and
# far above off-topic. Recheck it with Toni's #13 questions; it changes with the model.
RELEVANCE_CUTOFF = 0.1
FALLBACK_RESULTS = 5


class AgentState(TypedDict, total=False):
    # Kept across turns (with a checkpointer).
    messages: Annotated[list[AnyMessage], add_messages]
    # Plain JSON only (model_dump), so any checkpointer can store and load it safely.
    project: dict[str, Any] | None  # a FenceSpec
    # Input for one turn, next to the new HumanMessage.
    answers: dict[str, Any] | None
    # Reset by guardrail at the start of every turn.
    route: Literal["clarify", "respond", "retrieve"]
    query: str
    hits: list[dict[str, Any]]
    reranked: bool
    grade: Literal["ok", "weak"]
    rewrites: int
    facts: dict[str, Any]  # grounding.Facts
    bom: dict[str, Any] | None  # the latest materials list of the conversation
    bom_changed: bool  # this turn made (or cleared) `bom`: send it again (#21)
    bom_facts: dict[str, Any]  # grounding.Facts of `bom` only; replaced with each new list
    rewrite_failed: bool
    cart: list[dict[str, Any]]
    chips: dict[str, Any] | None
    notes: list[str]
    extra_lines: list[dict[str, Any]]  # chat_events.ExtraLine
    tool_rounds: int
    grounding_attempts: int
    grounding_problems: list[str]
    generate_failed: bool
    answer: str | None
    contact_martom: bool
    fallback: bool


@dataclass(frozen=True)
class AgentModels:
    """The model calls; production builds them in agent/factory.py, tests pass fakes."""

    guard: Runnable[Sequence[BaseMessage], Understanding]  # Haiku, structured output
    grade: Runnable[Sequence[BaseMessage], Grade]  # Haiku, structured output
    rewrite: Runnable[Sequence[BaseMessage], Rewrite]  # Haiku, structured output
    chat: BaseChatModel  # Sonnet; the graph binds the tools
    guard_model_name: str = "fake"
    chat_model_name: str = "fake"


@dataclass(frozen=True)
class AgentDeps:
    models: AgentModels
    retriever: Retriever
    catalogue: Catalogue
    pricer: BomPricer | None = None  # PostgresBomPricer; None: every line "cijena na upit"


def build_graph(
    deps: AgentDeps, *, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    """The compiled advisor graph; pass a checkpointer to keep conversations by thread_id."""
    chat = deps.models.chat.bind_tools([tool.schema() for tool in TOOLS])

    def llm_config(name: str, model: str) -> RunnableConfig:
        return {
            "run_name": name,
            "tags": ["agent", PROMPT_VERSION],
            "metadata": {"prompt_version": PROMPT_VERSION, "model": model},
        }

    async def guardrail(state: AgentState) -> dict[str, Any]:
        question = _last_customer_text(state)
        previous = _spec(state)
        answers = _answers(state.get("answers"))
        messages = [
            SystemMessage(GUARD_SYSTEM),
            HumanMessage(
                tagged("known_project", previous.model_dump(mode="json") if previous else {})
                + "\n"
                + tagged("customer_message", question)
            ),
        ]
        try:
            understanding = await deps.models.guard.ainvoke(
                messages, config=llm_config("guardrail_llm", deps.models.guard_model_name)
            )
        except Exception as exc:
            # Never fail a turn on the guardrail: search for the message as written.
            logger.warning("guardrail failed; searching as written", extra=_error(exc))
            understanding = _fallback_understanding(question)
        spec = update_spec(previous, understanding, answers)
        heights = None
        if understanding.request == "fence_eurofence":
            try:
                heights = await deps.catalogue.variant_heights(EUROFENCE_LISTING_ID)
            except Exception as exc:
                # Skip the "ne postoji" check; the search still finds the listing.
                logger.warning("eurofence heights unavailable", extra=_error(exc))
        plan = plan_turn(
            understanding, spec, follow_up=answers is not None, eurofence_heights=heights
        )
        # Saved whenever the plan built one, also before the fence type is known (R1-9).
        project = plan.spec.model_dump(mode="json") if plan.spec else state.get("project")
        return {
            "project": project,
            "answers": None,
            "route": plan.route,
            "query": plan.query or question,
            "hits": [],
            "reranked": False,
            "grade": "ok",
            "rewrites": 0,
            # `facts` (search and product results) stay: they are still in the history the
            # model sees, so it may repeat them (R1-3). The materials list and its numbers
            # (`bom_facts`) stay only while the fence is the same; a changed fence or one too
            # large makes the old list wrong, so it is cleared and must be recalculated (R2-1).
            **_bom_update(state, previous, plan.spec, clear=plan.contact_martom),
            "cart": [],
            "rewrite_failed": False,
            "chips": plan.chips.model_dump(mode="json") if plan.chips else None,
            "notes": plan.notes,
            "extra_lines": [line.model_dump(mode="json") for line in plan.extra_lines],
            "tool_rounds": 0,
            "grounding_attempts": 0,
            "grounding_problems": [],
            "generate_failed": False,
            "answer": plan.answer,
            "contact_martom": plan.contact_martom,
            "fallback": False,
        }

    async def clarify(state: AgentState) -> dict[str, Any]:
        answer = _join(state.get("answer") or "", *state.get("notes", []))
        return {"answer": answer, "messages": [AIMessage(answer)]}

    async def retrieve(state: AgentState) -> dict[str, Any]:
        try:
            retrieval = await deps.retriever(state["query"])
        except Exception as exc:
            logger.warning("retrieval failed; answering without products", extra=_error(exc))
            return {"hits": [], "reranked": False}
        hits = [hit_json(hit) for hit in retrieval.hits]
        # The hits come from the database, so their ids and titles are tool data the answer
        # may cite (R1-2); the scores are not.
        found = facts_from_output([_hit_facts(hit) for hit in hits])
        return {
            "hits": hits,
            "reranked": retrieval.reranked,
            "facts": _facts(state).merge(found).model_dump(),
        }

    async def grade(state: AgentState) -> dict[str, Any]:
        hits = state.get("hits", [])
        if not hits:
            return {"grade": "weak"}
        scores = [hit["relevance"] for hit in hits if hit["relevance"] is not None]
        if state.get("reranked") and scores and max(scores) < RELEVANCE_CUTOFF:
            return {"grade": "weak"}
        messages = [
            SystemMessage(GRADE_SYSTEM),
            HumanMessage(
                tagged("request", _last_customer_text(state))
                + "\n"
                + tagged("search_results", [_brief(hit) for hit in hits])
            ),
        ]
        try:
            result = await deps.models.grade.ainvoke(
                messages, config=llm_config("grade_llm", deps.models.guard_model_name)
            )
        except Exception as exc:
            # Keep the results: the rerank already ordered them, generate judges the rest.
            logger.warning("grading failed; keeping the results", extra=_error(exc))
            return {"grade": "ok"}
        return {"grade": "ok" if result.fits else "weak"}

    async def rewrite(state: AgentState) -> dict[str, Any]:
        messages = [
            SystemMessage(REWRITE_SYSTEM),
            HumanMessage(
                tagged("request", _last_customer_text(state))
                + "\n"
                + tagged("previous_query", state["query"])
            ),
        ]
        rewrites = state.get("rewrites", 0) + 1
        try:
            result = await deps.models.rewrite.ainvoke(
                messages, config=llm_config("rewrite_llm", deps.models.guard_model_name)
            )
        except Exception as exc:
            logger.warning("query rewrite failed; keeping the results", extra=_error(exc))
            return {"rewrites": rewrites, "rewrite_failed": True}
        return {"query": result.query, "rewrites": rewrites}

    async def generate(state: AgentState) -> dict[str, Any]:
        messages = [SystemMessage(_generate_context(state)), *state.get("messages", [])]
        try:
            reply = await chat.ainvoke(
                messages, config=llm_config("generate_llm", deps.models.chat_model_name)
            )
        except Exception as exc:
            logger.warning("generate failed; answering with search results", extra=_error(exc))
            return {"generate_failed": True}
        return {"messages": [reply]}

    async def tools(state: AgentState) -> dict[str, Any]:
        call_message = state["messages"][-1]
        assert isinstance(call_message, AIMessage)
        ctx = ToolContext(
            spec=_spec(state),
            extra_lines=[ExtraLine.model_validate(x) for x in state.get("extra_lines", [])],
            retriever=deps.retriever,
            catalogue=deps.catalogue,
            pricer=deps.pricer,
        )
        facts = _facts(state)
        bom = state.get("bom")
        bom_changed = state.get("bom_changed", False)
        bom_facts = _bom_facts(state)
        cart = list(state.get("cart", []))
        contact = state.get("contact_martom", False)
        results: list[ToolMessage] = []
        for call in call_message.tool_calls:
            name, call_id = call["name"], call["id"] or ""
            try:
                output = await _run_tool(name, call["args"], ctx)
            except FenceTooLargeToolError as exc:
                contact, bom, bom_changed, bom_facts = True, None, True, Facts()
                results.append(_tool_error(call_id, name, str(exc)))
                continue
            except ToolError as exc:
                results.append(_tool_error(call_id, name, str(exc)))
                continue
            except Exception as exc:
                # A database, retriever or pricer failure: the model answers without it.
                logger.warning("tool failed", extra={"tool": name, **_error(exc)})
                results.append(_tool_error(call_id, name, "the tool failed; answer without it"))
                continue
            # The cart echoes the model's own quantity back: never a fact (R1-11).
            skip = ("quantity",) if name == "add_to_cart" else ()
            if name == "calculate_fence_bom":
                # A new list replaces the old one's numbers, never adds to them (R2-1).
                bom, bom_changed, bom_facts = output, True, facts_from_output(output)
            else:
                facts = facts.merge(facts_from_output(output, skip_keys=skip))
            if name == "add_to_cart":
                cart.append(output["cart_line"])
            content = json.dumps(output, ensure_ascii=False)
            results.append(ToolMessage(content, tool_call_id=call_id, name=name))
        return {
            "messages": results,
            "facts": facts.model_dump(),
            "bom": bom,
            "bom_changed": bom_changed,
            "bom_facts": bom_facts.model_dump(),
            "cart": cart,
            "contact_martom": contact,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }

    async def grounding(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1] if state.get("messages") else None
        if state.get("generate_failed") or not isinstance(last, AIMessage):
            return _fallback(state, remove=None)
        text = last.text.strip()
        facts = _facts(state).merge(_bom_facts(state))
        result = check_grounding(text, facts, known_numbers=_customer_numbers(state))
        if text and not last.tool_calls and result.ok:
            return {"answer": _join(text, *state.get("notes", []))}
        problems = result.problems or ["the answer was empty or still calling tools"]
        logger.warning(
            "grounding check failed",
            extra={"problems": len(problems), "attempt": state.get("grounding_attempts", 0)},
        )
        if state.get("grounding_attempts", 0) < MAX_GROUNDING_RETRIES:
            return {
                "messages": [RemoveMessage(id=last.id or "")],
                "grounding_attempts": state.get("grounding_attempts", 0) + 1,
                "grounding_problems": problems,
                "tool_rounds": 0,
            }
        return _fallback(state, remove=last)

    def after_guardrail(state: AgentState) -> Literal["clarify", "retrieve"]:
        return "retrieve" if state["route"] == "retrieve" else "clarify"

    def after_grade(state: AgentState) -> Literal["rewrite", "generate"]:
        if state["grade"] == "weak" and state.get("rewrites", 0) < MAX_REWRITES:
            return "rewrite"
        return "generate"

    def after_rewrite(state: AgentState) -> Literal["retrieve", "generate"]:
        # A failed rewrite would search the same query again: answer with what we have.
        return "generate" if state.get("rewrite_failed") else "retrieve"

    def after_generate(state: AgentState) -> Literal["tools", "grounding"]:
        last = state["messages"][-1] if state.get("messages") else None
        if (
            not state.get("generate_failed")
            and isinstance(last, AIMessage)
            and last.tool_calls
            and state.get("tool_rounds", 0) < MAX_TOOL_ROUNDS
        ):
            return "tools"
        return "grounding"

    def after_grounding(state: AgentState) -> Literal["generate", "__end__"]:
        return END if state.get("answer") else "generate"

    builder = StateGraph(AgentState)
    builder.add_node("guardrail", guardrail)
    builder.add_node("clarify", clarify)
    builder.add_node("retrieve", retrieve)
    builder.add_node("grade", grade)
    builder.add_node("rewrite", rewrite)
    builder.add_node("generate", generate)
    builder.add_node("tools", tools)
    builder.add_node("grounding", grounding)
    builder.add_edge(START, "guardrail")
    builder.add_conditional_edges("guardrail", after_guardrail, ["clarify", "retrieve"])
    builder.add_edge("clarify", END)
    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges("grade", after_grade, ["rewrite", "generate"])
    builder.add_conditional_edges("rewrite", after_rewrite, ["retrieve", "generate"])
    builder.add_conditional_edges("generate", after_generate, ["tools", "grounding"])
    builder.add_edge("tools", "generate")
    builder.add_conditional_edges("grounding", after_grounding, ["generate", END])
    return builder.compile(checkpointer=checkpointer, name=GRAPH_NAME)


async def _run_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"unknown tool {name!r}")
    try:
        parsed = tool.args.model_validate(args)
    except ValidationError as exc:
        # Field names and messages only; the input values stay out of the reply.
        fields = ", ".join(".".join(map(str, e["loc"])) or "input" for e in exc.errors())
        raise ToolError(f"invalid arguments: {fields}") from None
    return await tool.handler(parsed, ctx)


def _tool_error(call_id: str, name: str, message: str) -> ToolMessage:
    return ToolMessage(f"error: {message}", tool_call_id=call_id, name=name, status="error")


def _generate_context(state: AgentState) -> str:
    spec = _spec(state)
    parts = [GENERATE_SYSTEM]
    if spec is not None and spec.kind is not None:
        parts.append(tagged("fence_spec", spec.model_dump(mode="json")))
        if state.get("bom") is None:
            parts.append(
                "There is no current materials list for this fence: call "
                "calculate_fence_bom for any quantity or total. Materials lists earlier in "
                "the conversation are out of date."
            )
    if state.get("grade") == "weak":
        parts.append(
            "The catalogue search found nothing that fits this request: don't recommend "
            "the results below; say so and suggest contacting Martom."
        )
    parts.append(tagged("search_results", state.get("hits", [])))
    problems = state.get("grounding_problems", [])
    if problems:
        parts.append(
            "Your previous draft broke the grounding rule. Write it again using only "
            "numbers from tool results:\n- " + "\n- ".join(problems)
        )
    return "\n\n".join(parts)


def _fallback(state: AgentState, *, remove: BaseMessage | None) -> dict[str, Any]:
    """Plain search results: titles and links, no prices (docs/PLAN.md)."""
    hits = state.get("hits", []) if state.get("grade") == "ok" else []
    lines = [f"- {hit['title']}: {hit['url']}" for hit in hits[:FALLBACK_RESULTS] if hit["url"]]
    text = "\n".join([copy.FALLBACK_INTRO, *lines]) if lines else copy.FALLBACK_NO_RESULTS
    answer = _join(text, *state.get("notes", []))
    messages: list[BaseMessage] = [RemoveMessage(id=remove.id)] if remove and remove.id else []
    messages.append(AIMessage(answer))
    return {"answer": answer, "fallback": True, "messages": messages}


def _spec(state: AgentState) -> FenceSpec | None:
    raw = state.get("project")
    return FenceSpec.model_validate(raw) if raw else None


def _hit_facts(hit: dict[str, Any]) -> dict[str, Any]:
    keys = ("listing_id", "title", "url", "product_type")
    return {key: hit[key] for key in keys}


def _customer_numbers(state: AgentState) -> set[Decimal]:
    """Numbers the customer gave (messages, chip answers via the spec) or the spec implies."""
    numbers: set[Decimal] = set()
    for message in state.get("messages", []):
        if isinstance(message, HumanMessage):
            numbers.update(numbers_in(message.text))
    spec = _spec(state)
    if spec is not None:
        values: list[float | int | None] = [
            spec.height_cm,
            spec.real_height_cm(),
            spec.total_length_m,
            spec.panel_width_cm,
            spec.requested_spacing_m,
            *spec.sides_m,
        ]
        if spec.sides_m:
            corners = len(spec.sides_m) if spec.closed else len(spec.sides_m) - 1
            values += [sum(spec.sides_m), len(spec.sides_m), corners]
        if spec.kind == "roll":
            values.append(POST_SPACING_CM / 100)  # "stupovi svaka 2 m" (fence-rules.md)
        if spec.kind == "panel":
            values.append(default_panel_width(spec))
        numbers.update(Decimal(str(value)) for value in values if value is not None)
    return numbers


def _bom_inputs(spec: FenceSpec | None) -> tuple[Any, ...] | None:
    """What the materials list depends on; a change makes the old list wrong."""
    if spec is None:
        return None
    return (
        spec.kind,
        spec.real_height_cm(),
        tuple(spec.sides_m),
        spec.closed,
        spec.posts,
        spec.panel_width_cm,
        spec.requested_spacing_m,
        spec.colour,
    )


def _bom_update(
    state: AgentState, previous: FenceSpec | None, spec: FenceSpec | None, *, clear: bool
) -> dict[str, Any]:
    had_bom = state.get("bom") is not None
    changed = spec is not None and _bom_inputs(spec) != _bom_inputs(previous)
    if clear or changed:
        return {"bom": None, "bom_changed": had_bom, "bom_facts": Facts().model_dump()}
    return {"bom": state.get("bom"), "bom_changed": False, "bom_facts": state.get("bom_facts")}


def _bom_facts(state: AgentState) -> Facts:
    return Facts.model_validate(state.get("bom_facts") or {})


def _facts(state: AgentState) -> Facts:
    return Facts.model_validate(state.get("facts") or {})


def _last_customer_text(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message.text
    return ""


def _answers(raw: dict[str, Any] | None) -> ClarifyAnswers | None:
    return ClarifyAnswers.model_validate(raw) if raw is not None else None


def _fallback_understanding(question: str) -> Understanding:
    return Understanding(
        in_scope=True,
        request="product",
        asks_gate=False,
        asks_installation=False,
        asks_concrete=False,
        wants_concrete_posts=False,
        height=None,
        lengths=[],
        layout="not_stated",
        colour=None,
        panel_width=None,
        post_spacing=None,
        search_query=question,
    )


def _brief(hit: dict[str, Any]) -> dict[str, Any]:
    return {"title": hit["title"], "product_type": hit["product_type"]}


def _error(exc: BaseException) -> dict[str, str]:
    # The type only: messages can quote customer text.
    return {"error": type(exc).__name__}


def _join(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)
