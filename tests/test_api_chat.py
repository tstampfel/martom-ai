"""POST /api/chat (#21): the real graph with fake models, streamed through FastAPI."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from tests.agent_fakes import (
    EUROFENCE,
    PANEL,
    PANEL_ID,
    FakeCatalogue,
    FakeChat,
    FakePricer,
    FakeRetriever,
    Recorder,
    tool_call,
    understanding,
)

from martom.agent.graph import AgentDeps, AgentModels, build_graph
from martom.agent.prompts import Grade, Rewrite
from martom.api import chat_copy
from martom.api.app import create_app
from martom.api.chat import (
    MAX_BODY_BYTES,
    MAX_CHOICE_CHARS,
    MAX_CHOICES,
    MAX_MESSAGE_CHARS,
    MAX_SIDES,
    ChatService,
)
from martom.api.threads import LocalThreadLocks, is_wellformed_thread_id, new_thread_id
from martom.settings import Settings

SCHEMA = json.loads(
    (Path(__file__).parents[1] / "docs" / "api" / "chat-events.schema.json").read_text()
)
SECRET_TEXT = "Ograda za Marka u ulici Tajna 7"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ADMIN_TOKEN", "APP_ENV", "ANTHROPIC_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


async def healthy() -> None:
    return None


def make_service(
    guard: Recorder,
    replies: Iterable[AIMessage] = (),
    *,
    retriever: Any = None,
    locks: LocalThreadLocks | None = None,
) -> ChatService:
    models = AgentModels(
        guard=RunnableLambda(guard),
        grade=RunnableLambda(Recorder(Grade(fits=True))),
        rewrite=RunnableLambda(Recorder(Rewrite(query="ograda panel"))),
        chat=FakeChat(messages=iter(replies)),
    )
    deps = AgentDeps(
        models=models,
        retriever=retriever or FakeRetriever(),
        catalogue=FakeCatalogue(PANEL, EUROFENCE),
        pricer=FakePricer(),
    )
    graph = build_graph(deps, checkpointer=InMemorySaver())
    return ChatService(graph=graph, locks=locks or LocalThreadLocks())


def make_app(service: ChatService | None) -> Any:
    checks = {"postgres": healthy, "redis": healthy}
    return create_app(Settings(), health_checks=checks, chat_service=service)


def client(service: ChatService | None) -> TestClient:
    return TestClient(make_app(service))


def events_of(response: httpx.Response) -> list[dict[str, Any]]:
    events = []
    for block in response.text.split("\n\n"):
        if block.strip():
            assert block.startswith("data: "), block
            events.append(json.loads(block.removeprefix("data: ")))
    return events


def post(test_client: TestClient, body: Any) -> list[dict[str, Any]]:
    response = test_client.post("/api/chat", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return events_of(response)


def bom_turn_service(**kwargs: Any) -> ChatService:
    return make_service(
        Recorder(understanding(height="153 cm", lengths=["25 m"], layout="straight")),
        [
            tool_call("get_product", {"listing_id": PANEL_ID}),
            tool_call("calculate_fence_bom"),
            AIMessage("Ukupno 905,10 KM."),
        ],
        **kwargs,
    )


# --- the stream -----------------------------------------------------------------------


def test_every_event_of_a_bom_turn_matches_the_committed_schema() -> None:
    events = post(client(bom_turn_service()), {"message": "Panelna ograda 25 m, 153 cm"})
    for event in events:
        jsonschema.validate(event, SCHEMA)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "step"
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1
    assert kinds[-3:-1] == ["product_cards", "bom"]
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Ukupno 905,10 KM."
    bom = next(e for e in events if e["type"] == "bom")
    assert bom["total"] == "905.10"
    cards = next(e for e in events if e["type"] == "product_cards")["cards"]
    assert [card["listing_id"] for card in cards] == [PANEL_ID]
    steps = [(e["id"], e["status"]) for e in events if e["type"] == "step"]
    assert steps == [
        ("parse", "working"),
        ("parse", "done"),
        ("search", "working"),
        ("search", "done"),
        ("bom", "done"),
    ]


def test_a_clarify_turn_sends_chips_and_matches_the_schema() -> None:
    service = make_service(
        Recorder(understanding(height="150 cm", lengths=["25 m"], layout="around"))
    )
    events = post(client(service), {"message": "Ograda oko dvorišta, 25 m, oko 150 cm"})
    for event in events:
        jsonschema.validate(event, SCHEMA)
    kinds = [event["type"] for event in events]
    assert kinds[-2:] == ["chips", "done"]
    assert {
        "type": "step",
        "id": "clarify",
        "text": chat_copy.STEP_CLARIFY,
        "status": "needs_input",
    } in events
    assert "bom" not in kinds


def test_the_stream_is_not_cached_or_buffered() -> None:
    response = client(bom_turn_service()).post("/api/chat", json={"message": "ograda 25 m"})
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"


def test_the_first_token_arrives_within_two_seconds() -> None:
    # Done when (docs/PLAN.md, M4-4): first token < 2 s locally, with the fake models.
    test_client = client(bom_turn_service())
    start = time.perf_counter()
    first_token = None
    with test_client.stream("POST", "/api/chat", json={"message": "Panelna ograda 25 m"}) as r:
        for line in r.iter_lines():
            if line.startswith("data: ") and json.loads(line[6:])["type"] == "token":
                first_token = time.perf_counter() - start
                break
    assert first_token is not None
    assert first_token < 2.0


# --- threads --------------------------------------------------------------------------


def test_a_first_message_gets_a_new_server_issued_thread() -> None:
    events = post(client(bom_turn_service()), {"message": "ograda", "thread_id": None})
    thread_id = events[-1]["thread_id"]
    assert is_wellformed_thread_id(thread_id)


def test_the_returned_thread_continues_the_conversation() -> None:
    service = bom_turn_service()
    test_client = client(service)
    first = post(test_client, {"message": "Panelna ograda 25 m, 153 cm"})
    thread_id = first[-1]["thread_id"]
    second = post(test_client, {"message": "Hvala", "thread_id": thread_id})
    assert second[-1]["thread_id"] == thread_id
    state = asyncio.run(service.graph.aget_state({"configurable": {"thread_id": thread_id}}))
    humans = [m for m in state.values["messages"] if m.type == "human"]
    assert [m.text for m in humans] == ["Panelna ograda 25 m, 153 cm", "Hvala"]


@pytest.mark.parametrize("sent", [new_thread_id(), "my-own-thread", "1"])
def test_an_unknown_or_client_made_thread_id_is_never_adopted(sent: str) -> None:
    service = bom_turn_service()
    events = post(client(service), {"message": "ograda", "thread_id": sent})
    issued = events[-1]["thread_id"]
    assert issued != sent
    assert is_wellformed_thread_id(issued)
    assert not asyncio.run(service.thread_exists(sent))


def test_a_busy_thread_answers_an_internal_error() -> None:
    locks = LocalThreadLocks(timeout=0.05)
    service = bom_turn_service(locks=locks)
    test_client = client(service)
    thread_id = post(test_client, {"message": "ograda"})[-1]["thread_id"]

    async def hold_and_post() -> list[dict[str, Any]]:
        transport = httpx.ASGITransport(app=test_client.app)
        async with locks.hold(thread_id), httpx.AsyncClient(transport=transport) as http:
            response = await http.post(
                "http://test/api/chat", json={"message": "opet", "thread_id": thread_id}
            )
        return events_of(response)

    events = asyncio.run(hold_and_post())
    assert [e["type"] for e in events][-2:] == ["error", "done"]
    assert events[-2]["kind"] == "internal"
    assert events[-1]["thread_id"] == thread_id


def test_two_requests_on_one_thread_both_keep_their_turn() -> None:
    class SlowRetriever(FakeRetriever):
        async def __call__(self, query: str) -> Any:
            await asyncio.sleep(0.05)
            return await super().__call__(query)

    replies = [AIMessage("Evo prijedloga.") for _ in range(3)]
    service = make_service(
        Recorder(understanding(request="product")), replies, retriever=SlowRetriever()
    )
    app = make_app(service)
    thread_id = post(TestClient(app), {"message": "prvo"})[-1]["thread_id"]

    async def both() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport) as http:
            await asyncio.gather(
                *(
                    http.post("http://test/api/chat", json={"message": m, "thread_id": thread_id})
                    for m in ("drugo", "treće")
                )
            )

    asyncio.run(both())
    state = asyncio.run(service.graph.aget_state({"configurable": {"thread_id": thread_id}}))
    messages = state.values["messages"]
    humans = sorted(m.text for m in messages if m.type == "human")
    assert humans == sorted(["prvo", "drugo", "treće"])
    # Without the lock both turns start from the same checkpoint and the last writer drops
    # the other turn's answer: every question must be followed by its own answer.
    assert [m.type for m in messages] == ["human", "ai"] * 3


# --- errors ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"message": "ograda", "answers": {"sides": [400], "closed": False}},
        {"message": ""},
        {"message": "ograda", "corners": 2},
        {"message": "x" * (MAX_MESSAGE_CHARS + 1)},
        ["not", "an", "object"],
    ],
)
def test_an_invalid_request_is_an_error_event_and_starts_nothing(body: Any) -> None:
    service = bom_turn_service()
    events = post(client(service), body)
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[0] == {"type": "error", "kind": "invalid_request", "retry_after_seconds": None}
    assert not asyncio.run(service.thread_exists(events[1]["thread_id"]))


def test_invalid_json_is_an_invalid_request() -> None:
    response = client(bom_turn_service()).post(
        "/api/chat", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert [e["type"] for e in events_of(response)] == ["error", "done"]


def test_an_invalid_request_on_a_real_thread_keeps_its_id() -> None:
    test_client = client(bom_turn_service())
    thread_id = post(test_client, {"message": "ograda"})[-1]["thread_id"]
    events = post(test_client, {"message": "", "thread_id": thread_id})
    assert events[-1] == {"type": "done", "thread_id": thread_id}


def test_without_an_advisor_every_turn_is_an_internal_error() -> None:
    events = post(client(None), {"message": "ograda"})
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[0]["kind"] == "internal"


def test_a_graph_failure_is_an_internal_error_without_customer_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenGraph:
        checkpointer = InMemorySaver()

        async def astream(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(SECRET_TEXT)
            yield  # an async generator

    service = ChatService(graph=BrokenGraph(), locks=LocalThreadLocks())  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="martom.api"):
        events = post(client(service), {"message": SECRET_TEXT})
    assert [e["type"] for e in events][-2:] == ["error", "done"]
    assert events[-2]["kind"] == "internal"
    assert SECRET_TEXT not in caplog.text


def test_a_turn_logs_counts_and_timings_never_the_text_or_thread_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="martom.api"):
        events = post(client(bom_turn_service()), {"message": SECRET_TEXT})
    thread_id = events[-1]["thread_id"]
    finished = [r for r in caplog.records if r.getMessage() == "chat turn finished"]
    assert len(finished) == 1
    record = finished[0]
    assert record.outcome == "ok"  # type: ignore[attr-defined]
    assert record.new_thread is True  # type: ignore[attr-defined]
    assert record.bom_sent is True  # type: ignore[attr-defined]
    assert record.cards == 1  # type: ignore[attr-defined]
    assert record.first_token_ms is not None  # type: ignore[attr-defined]
    assert SECRET_TEXT not in caplog.text
    assert thread_id not in caplog.text


def test_cors_allows_the_chat_from_pages_dev() -> None:
    response = client(bom_turn_service()).options(
        "/api/chat",
        headers={
            "Origin": "https://martom-savjetnik.pages.dev",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://martom-savjetnik.pages.dev"


def test_the_lifespan_opens_the_service_once_and_closes_it_on_shutdown() -> None:
    opened: list[str] = []

    @asynccontextmanager
    async def opener(settings: Settings) -> AsyncGenerator[ChatService]:
        opened.append("open")
        yield bom_turn_service()
        opened.append("close")

    checks = {"postgres": healthy, "redis": healthy}
    app = create_app(Settings(), health_checks=checks, open_chat=opener)
    with TestClient(app) as test_client:
        assert opened == ["open"]
        events = post(test_client, {"message": "Panelna ograda 25 m, 153 cm"})
        assert events[-2]["type"] == "bom"
        post(test_client, {"message": "Hvala"})
        assert opened == ["open"]
    assert opened == ["open", "close"]


@pytest.mark.parametrize(
    "answers",
    [
        {"sides": [1.0] * (MAX_SIDES + 1), "closed": False},
        {"choices": {f"q{i}": "x" for i in range(MAX_CHOICES + 1)}},
        {"choices": {"posts": "x" * (MAX_CHOICE_CHARS + 1)}},
        {"choices": {"x" * (MAX_CHOICE_CHARS + 1): "plate"}},
    ],
)
def test_oversized_answers_are_refused_before_the_advisor(answers: dict[str, Any]) -> None:
    service = bom_turn_service()
    events = post(client(service), {"message": "ograda", "answers": answers})
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[0]["kind"] == "invalid_request"


def refusal_reasons(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.reason for r in caplog.records if r.getMessage() == "chat request refused"]  # type: ignore[attr-defined]


def test_a_body_over_the_size_limit_is_refused_unread(caplog: pytest.LogCaptureFixture) -> None:
    body = json.dumps({"message": "ograda", "padding": "x" * MAX_BODY_BYTES}).encode()
    with caplog.at_level(logging.INFO, logger="martom.api"):
        response = client(bom_turn_service()).post(
            "/api/chat", content=body, headers={"content-type": "application/json"}
        )
    events = events_of(response)
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[0]["kind"] == "invalid_request"
    assert refusal_reasons(caplog) == ["too_large"]


def test_a_body_without_content_length_is_still_capped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def chunks() -> Iterable[bytes]:
        yield b'{"message": "'
        for _ in range(MAX_BODY_BYTES // 1000 + 2):
            yield b"x" * 1000
        yield b'"}'

    with caplog.at_level(logging.INFO, logger="martom.api"):
        response = client(bom_turn_service()).post(
            "/api/chat", content=chunks(), headers={"content-type": "application/json"}
        )
    assert "content-length" not in response.request.headers
    assert [e["type"] for e in events_of(response)] == ["error", "done"]
    assert refusal_reasons(caplog) == ["too_large"]
