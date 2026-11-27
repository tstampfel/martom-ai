"""The real advisor graph on the Postgres checkpointer (see conftest.database_url).

Fake models, catalogue and retriever as in test_agent_graph.py: no network, no paid API.
"""

from collections.abc import Iterable
from typing import Any

import psycopg
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from tests.agent_fakes import (
    EUROFENCE,
    PANEL,
    FakeCatalogue,
    FakeChat,
    FakeRetriever,
    Recorder,
    tool_call,
    understanding,
)

from martom.agent.factory import open_agent_graph
from martom.agent.graph import AgentDeps, AgentModels
from martom.agent.prompts import Grade, Rewrite
from martom.checkpointer import CheckpointerNotReadyError, setup_checkpointer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def fresh_deps(guard: Recorder, replies: Iterable[AIMessage] = ()) -> AgentDeps:
    """New fakes each time, so nothing carries over in memory between the two 'processes'."""
    return AgentDeps(
        models=AgentModels(
            guard=RunnableLambda(guard),
            grade=RunnableLambda(Recorder(Grade(fits=True))),
            rewrite=RunnableLambda(Recorder(Rewrite(query="ograda panel"))),
            chat=FakeChat(messages=iter(replies)),
        ),
        retriever=FakeRetriever(),
        catalogue=FakeCatalogue(PANEL, EUROFENCE),
    )


def config(thread: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}


def turn(text: str, answers: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"messages": [HumanMessage(text)], "answers": answers}


def checkpoint_count(database_url: str, thread: str) -> int:
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread,)
        ).fetchone()
    return 0 if row is None else row[0]


async def test_thread_resumes_after_a_restart(database_url: str) -> None:
    setup_checkpointer(database_url)  # what `martom migrate` does before the API starts

    # First "process": the customer describes the fence and gets the chips.
    first_guard = Recorder(understanding(height="153", lengths=["25 m"], layout="around"))
    async with open_agent_graph(fresh_deps(first_guard), database_url) as graph:
        first = await graph.ainvoke(turn("Ograda oko dvorišta 25 m, 153 cm"), config("yard"))
    assert first["chips"] is not None
    assert checkpoint_count(database_url, "yard") > 0

    # Second "process": new pool, new saver, new graph, new fakes. Only Postgres knows the
    # fence spec, so the BOM can only come out right if the thread was loaded from it.
    second_guard = Recorder(understanding(request="other", search_query=""))
    replies = [tool_call("calculate_fence_bom"), AIMessage("Evo popisa.")]
    answers = {"choices": {"posts": "plate", "color": "antracit"}, "sides": [10, 15]}
    async with open_agent_graph(fresh_deps(second_guard, replies), database_url) as graph:
        second = await graph.ainvoke(turn("10 m i 15 m, antracit", answers), config("yard"))
        other = await graph.aget_state(config("another-customer"))

    assert second["chips"] is None
    assert second["project"]["sides_m"] == [10, 15]
    assert second["bom"]["summary"] == "25 m · panel 153 cm · antracit"
    clips = next(line for line in second["bom"]["lines"] if line["item"] == "pvc_clip")
    # The same count as test_thread_keeps_the_fence_spec_between_turns (InMemorySaver).
    assert clips["quantity"] == 11 * 4 + 4
    customer_texts = [m.content for m in second["messages"] if isinstance(m, HumanMessage)]
    assert customer_texts == ["Ograda oko dvorišta 25 m, 153 cm", "10 m i 15 m, antracit"]
    # Another thread starts empty.
    assert other.values == {}


async def test_open_agent_graph_refuses_a_database_without_the_tables(database_url: str) -> None:
    # No `martom migrate`: opening fails at startup instead of creating the tables there
    # or failing every chat turn later (CLAUDE.md, "The one exception").
    guard = Recorder(understanding(height="153", lengths=["25 m"], layout="around"))
    with pytest.raises(CheckpointerNotReadyError, match="martom migrate"):
        async with open_agent_graph(fresh_deps(guard), database_url):
            pytest.fail("the graph must not open without the checkpointer tables")

    with psycopg.connect(database_url) as conn:
        row = conn.execute("SELECT to_regclass('checkpoints')").fetchone()
    assert row == (None,)
