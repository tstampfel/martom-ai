"""The real models and retriever for the advisor graph (tests pass fakes instead).

Haiku (`FAST_MODEL`) runs the guardrail, the grading and the query rewrite with
structured output: thinking disabled at effort "low", since copying values and a yes/no
need no reasoning (Haiku 5.5 accepts disabled thinking at effort "high" or below). Sonnet
(`AGENT_MODEL`) writes the answer and calls the tools at effort "medium" (the API
guidance for multistep tool use on Sonnet 5.5). Sonnet 5.5 rejects disabled thinking, so
its thinking stays at the default; neither model gets a `temperature` (both reject
non-default sampling values).

Without ANTHROPIC_API_KEY there is no advisor: `make_agent_models` returns None and the
caller (#21) answers with an error instead of guessing.

`open_agent_graph` compiles the graph with the Postgres checkpointer (#124), so a
conversation survives an API restart. Tests compile with `InMemorySaver` instead.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
from langchain_anthropic import ChatAnthropic
from langgraph.graph.state import CompiledStateGraph

from martom.agent.graph import AgentDeps, AgentModels, build_graph
from martom.agent.prompts import Grade, Rewrite
from martom.agent.tools import Retriever
from martom.agent.understand import Understanding
from martom.checkpointer import open_checkpointer
from martom.embeddings import Embedder
from martom.search.query_parser import QueryParser
from martom.search.rerank import Reranker
from martom.search.retrieve import Retrieval, retrieve
from martom.settings import Settings

FAST_MAX_TOKENS = 1024  # a structured reply of a few fields
AGENT_MAX_TOKENS = 4096  # thinking plus a short Bosnian answer
FAST_TIMEOUT_SECONDS = 10.0
AGENT_TIMEOUT_SECONDS = 60.0


def make_agent_models(settings: Settings) -> AgentModels | None:
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        return None
    haiku = ChatAnthropic(
        model=settings.fast_model,
        api_key=key,
        max_tokens=FAST_MAX_TOKENS,
        thinking={"type": "disabled"},
        effort="low",
        max_retries=1,
        default_request_timeout=FAST_TIMEOUT_SECONDS,
    )
    sonnet = ChatAnthropic(
        model=settings.agent_model,
        api_key=key,
        max_tokens=AGENT_MAX_TOKENS,
        effort="medium",
        max_retries=2,
        default_request_timeout=AGENT_TIMEOUT_SECONDS,
    )
    return AgentModels(
        guard=haiku.with_structured_output(Understanding, method="json_schema"),  # type: ignore[arg-type]
        grade=haiku.with_structured_output(Grade, method="json_schema"),  # type: ignore[arg-type]
        rewrite=haiku.with_structured_output(Rewrite, method="json_schema"),  # type: ignore[arg-type]
        chat=sonnet,
        guard_model_name=settings.fast_model,
        chat_model_name=settings.agent_model,
    )


def make_retriever(
    conn: psycopg.AsyncConnection,
    *,
    embedder: Embedder,
    parser: QueryParser | None,
    reranker: Reranker | None,
) -> Retriever:
    """The graph's Retrieve step: search.retrieve with live settings (its own fallbacks)."""

    async def run(query: str) -> Retrieval:
        return await retrieve(conn, query, embedder=embedder, parser=parser, reranker=reranker)

    return run


@asynccontextmanager
async def open_agent_graph(
    deps: AgentDeps, database_url: str
) -> AsyncGenerator[CompiledStateGraph]:
    """Yield the advisor graph with the Postgres checkpointer; closes its pool on exit.

    Open it once per process (FastAPI's lifespan, #21) and call the graph with
    `{"configurable": {"thread_id": ...}}`: each thread's messages and fence spec are
    loaded from Postgres, so a new process continues the same conversation. It never
    creates the checkpointer tables; `martom migrate` does (CLAUDE.md, "The one
    exception"), and opening on a database without them raises
    `CheckpointerNotReadyError`.

    For the chat endpoint (#21): a thread holds a customer's chat text, so its id must
    be an unguessable token the server issues (e.g. `secrets.token_urlsafe(32)`), never
    a value the client picks. Two turns on one thread at the same time are
    last-writer-wins (a double tap can lose a turn), so serialise turns per thread.
    """
    async with open_checkpointer(database_url) as checkpointer:
        yield build_graph(deps, checkpointer=checkpointer)
