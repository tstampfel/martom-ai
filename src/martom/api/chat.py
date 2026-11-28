"""POST /api/chat: one customer message in, a Server-Sent Events stream of chat events out.

The contract is docs/api/chat-events.md (models in chat_events.py and chat_request.py).
Every reply ends with exactly one `done` event carrying the thread id, also after an
`error`, so the browser has one code path.

A turn (`_turn_stream`):

1. `step` "parse" working, at once, so the customer sees the advisor start.
2. The turn lock for the thread (api/threads.py), then the advisor graph with
   `stream_mode="updates"`: after each node, `StepTracker` may send a step.
3. The graph's final state becomes the reply (`chat_turn.final_events`): the grounded
   answer as `token` chunks, then `chips`, or `product_cards` and `bom`.
4. `done`.

Steps 1-3 run in a task of their own and hand events to the stream through a queue, so
the stream can send a `: ping` comment every 15 s while the turn waits (CloudFront cuts
a stream that is silent for 60 s). `StreamingResponse` sends each yielded string as
soon as it is produced; an async generator here is like an `async function*` in JS. If
the browser disconnects, the generator is closed at its current `yield` and its
`finally` cancels the turn's task, which leaves the lock's `async with` and releases it.

Nothing logs the customer's text or the thread id (anyone holding the id can continue
the chat): one `chat turn finished` line per turn with counts and timings, and the
thread as a short hash (`thread_hash`).
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from martom.api.chat_events import ChatEvent, DoneEvent, ErrorEvent, TokenEvent
from martom.api.chat_request import ChatRequest
from martom.api.chat_turn import StepTracker, final_events
from martom.api.threads import (
    ThreadBusyError,
    ThreadLocks,
    is_wellformed_thread_id,
    new_thread_id,
    thread_log_id,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# The longest message the advisor reads (Tomislav decides the number, #21). The request
# schema has no limit, so this check lives here and answers `invalid_request`.
MAX_MESSAGE_CHARS = 2000
# Request size caps, checked here so the request schema (the contract) stays as it is.
# A real request is well under 1 KB; anything bigger is refused before it is parsed,
# and oversized answers before they reach the graph or its checkpoint.
MAX_BODY_BYTES = 16_384
MAX_SIDES = 50  # far more sides than any yard; the BOM caps the total at 1,000 m anyway
MAX_CHOICES = 10  # one per single question of a chips event
MAX_CHOICE_CHARS = 64  # question ids and option values are short keys
# A comment line (SSE ignores it): sent while a turn waits, so proxies see bytes flow.
HEARTBEAT = ": ping\n\n"
HEARTBEAT_SECONDS = 15.0
STREAM_HEADERS = {
    "Cache-Control": "no-store",
    # Tells a buffering proxy (nginx and friends) to pass each event on at once.
    "X-Accel-Buffering": "no",
}


@dataclass(frozen=True)
class ChatService:
    """What the endpoint needs: the compiled advisor graph and the thread turn locks.

    Built once per process by `chat_service.open_chat_service` (FastAPI's lifespan);
    tests build one with fake models and an in-memory checkpointer.
    """

    graph: CompiledStateGraph
    locks: ThreadLocks

    async def thread_exists(self, thread_id: object) -> bool:
        """True only for a well-formed id that already has a checkpoint."""
        if not is_wellformed_thread_id(thread_id):
            return False
        checkpointer = self.graph.checkpointer
        if checkpointer is None or isinstance(checkpointer, bool):
            return False
        config = {"configurable": {"thread_id": thread_id}}
        return await checkpointer.aget_tuple(config) is not None  # type: ignore[arg-type]


@router.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    started = time.perf_counter()
    service: ChatService | None = getattr(request.app.state, "chat_service", None)
    raw = await _read_capped(request)
    if raw is None:
        return await _refuse(service, None, reason="too_large")
    try:
        data = _decode_json(raw)
    except (ValueError, RecursionError):
        # RecursionError is not a ValueError: very deep nesting must not become a 500.
        return await _refuse(service, None, reason="json")
    try:
        body = ChatRequest.model_validate(data)
    except ValidationError:
        return await _refuse(service, data, reason="schema")
    if len(body.message) > MAX_MESSAGE_CHARS:
        return await _refuse(service, data, reason="too_long")
    if body.answers is not None and _answers_too_large(body):
        return await _refuse(service, data, reason="answers_too_large")

    if service is None:
        logger.warning("chat turn refused: no advisor configured")
        return _stream(_events_only([ErrorEvent(kind="internal")], new_thread_id()))
    existing = await _thread_exists(service, body.thread_id)
    if existing is None:
        # The checkpointer can't be read (database down): an error event, never a 500.
        # A fresh id, because an id that wasn't confirmed is never sent back.
        return _stream(_events_only([ErrorEvent(kind="internal")], new_thread_id()))
    thread_id = body.thread_id if existing and body.thread_id else new_thread_id()
    return _stream(_turn_stream(service, body, thread_id, new_thread=not existing, started=started))


def _decode_json(raw: bytes) -> object:
    return json.loads(raw)


async def _thread_exists(service: ChatService, thread_id: object) -> bool | None:
    """Whether the thread has a checkpoint; None when the checkpointer fails."""
    try:
        return await service.thread_exists(thread_id)
    except Exception as exc:
        logger.warning("thread lookup failed", extra={"error": type(exc).__name__})
        return None


async def _read_capped(request: Request) -> bytes | None:
    """The body, or None when it is over MAX_BODY_BYTES (the rest is never read)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def _answers_too_large(body: ChatRequest) -> bool:
    answers = body.answers
    assert answers is not None
    return (
        len(answers.sides) > MAX_SIDES
        or len(answers.choices) > MAX_CHOICES
        or any(
            len(key) > MAX_CHOICE_CHARS or len(value) > MAX_CHOICE_CHARS
            for key, value in answers.choices.items()
        )
    )


async def _refuse(service: ChatService | None, data: object, *, reason: str) -> StreamingResponse:
    """An `invalid_request` reply. Keeps the thread id only when it names a real thread."""
    sent = data.get("thread_id") if isinstance(data, dict) else None
    keep = service is not None and bool(await _thread_exists(service, sent))
    logger.info("chat request refused", extra={"reason": reason})
    thread_id = sent if keep and isinstance(sent, str) else new_thread_id()
    return _stream(_events_only([ErrorEvent(kind="invalid_request")], thread_id))


def _stream(events: AsyncGenerator[str]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers=STREAM_HEADERS)


def _sse(event: ChatEvent) -> str:
    # One event per SSE message: a `data:` line and a blank line. JSON has no raw
    # newlines (they are escaped inside strings), so one line always holds it.
    return f"data: {event.model_dump_json()}\n\n"


async def _events_only(events: Iterable[ChatEvent], thread_id: str) -> AsyncGenerator[str]:
    for event in events:
        yield _sse(event)
    yield _sse(DoneEvent(thread_id=thread_id))


async def _turn_stream(
    service: ChatService,
    body: ChatRequest,
    thread_id: str,
    *,
    new_thread: bool,
    started: float,
) -> AsyncGenerator[str]:
    """The turn's events; a heartbeat comment whenever nothing was sent for a while.

    The turn runs in its own task (`_run_turn`) and hands events over through a queue,
    so this generator can send `: ping` every HEARTBEAT_SECONDS while the turn waits for
    the thread lock or a model: CloudFront drops a stream that sends no bytes for 60 s
    (infra/cloudfront.tf `origin_read_timeout`). If the browser goes away, the `finally`
    cancels the task, which releases the thread lock.
    """
    stats: dict[str, Any] = {
        "thread_hash": thread_log_id(thread_id),
        "new_thread": new_thread,
        "outcome": "cancelled",  # until the reply has been sent in full
        "route": None,
        "fallback": None,
        "tokens": 0,
        "cards": 0,
        "bom_sent": False,
        "first_token_ms": None,
    }
    queue: asyncio.Queue[ChatEvent | None] = asyncio.Queue()
    task = asyncio.create_task(_run_turn(service, body, thread_id, queue, stats))
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield HEARTBEAT
                continue
            if event is None:  # the turn has finished
                break
            if isinstance(event, TokenEvent):
                stats["tokens"] += 1
                if stats["first_token_ms"] is None:
                    stats["first_token_ms"] = _ms_since(started)
            elif event.type == "product_cards":
                stats["cards"] = len(event.cards)
            elif event.type == "bom":
                stats["bom_sent"] = True
            yield _sse(event)
        yield _sse(DoneEvent(thread_id=thread_id))
        stats["outcome"] = stats.pop("turn_outcome", "ok")
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        stats.pop("turn_outcome", None)
        logger.info("chat turn finished", extra={**stats, "total_ms": _ms_since(started)})


async def _run_turn(
    service: ChatService,
    body: ChatRequest,
    thread_id: str,
    queue: "asyncio.Queue[ChatEvent | None]",
    stats: dict[str, Any],
) -> None:
    """Run the graph under the thread lock and put every event on `queue`, then None.

    The None always comes (`finally`), so the stream never waits for a turn that died.
    """
    try:
        await _run_graph(service, body, thread_id, queue, stats)
    finally:
        queue.put_nowait(None)


async def _run_graph(
    service: ChatService,
    body: ChatRequest,
    thread_id: str,
    queue: "asyncio.Queue[ChatEvent | None]",
    stats: dict[str, Any],
) -> None:
    tracker = StepTracker()
    await queue.put(tracker.start())
    reply: list[ChatEvent]
    try:
        async with service.locks.hold(thread_id):
            config = {"configurable": {"thread_id": thread_id}}
            turn_input = {
                "messages": [HumanMessage(body.message)],
                "answers": body.answers.model_dump(mode="json") if body.answers else None,
            }
            async for update in service.graph.astream(
                turn_input,
                config,  # type: ignore[arg-type]
                stream_mode="updates",
            ):
                for node, changes in update.items():
                    for step in tracker.update(node, changes if isinstance(changes, dict) else {}):
                        await queue.put(step)
            snapshot = await service.graph.aget_state(config)  # type: ignore[arg-type]
            stats["route"] = snapshot.values.get("route")
            stats["fallback"] = snapshot.values.get("fallback")
            reply = final_events(snapshot.values)
            stats["turn_outcome"] = "ok"
    except ThreadBusyError:
        stats["turn_outcome"] = "busy"
        reply = [ErrorEvent(kind="internal")]
    except Exception as exc:
        # The type only: an error message can quote the customer's text.
        logger.warning("chat turn failed", extra={"error": type(exc).__name__})
        stats["turn_outcome"] = "error"
        reply = [ErrorEvent(kind="internal")]
    for event in reply:
        await queue.put(event)


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
