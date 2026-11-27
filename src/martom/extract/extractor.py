"""Call Claude Haiku for each listing, with retries, a concurrency limit and trace tags.

How the pieces fit:

- `make_structured_model(settings)` builds the LangChain model: `ChatAnthropic` for
  `claude-haiku-5-5`, wrapped by `with_structured_output(ExtractedProduct,
  method="json_schema")`. That sends the Pydantic schema as the API's structured-output
  format (`output_config.format`) and parses the reply back into an `ExtractedProduct`.
  json_schema rather than a forced tool call, because Haiku 5.5 thinks by default and the
  API doesn't allow forcing a tool while thinking. No `temperature` either: Haiku 5.5
  rejects non-default sampling settings.
- `Extractor` takes any Runnable that maps messages to an `ExtractedProduct`, so tests pass
  a fake one and never call the API (CLAUDE.md: no paid API calls in CI).
- Retries, in two layers. HTTP problems (429, 5xx, timeouts, connection errors) are
  retried by the Anthropic SDK inside `ChatAnthropic` (`max_retries`, with backoff). A
  reply that doesn't validate against the schema is retried by LangChain's `with_retry`
  (a fresh call, up to `max_attempts`). A listing that still fails is reported as an
  error and the run goes on with the others.
- Concurrency: an `asyncio.Semaphore` lets at most `concurrency` calls (4 by default) run
  at once; the rest wait their turn. Think `p-limit(4)` in JS.
- LangSmith: LangChain traces every call by itself when `LANGSMITH_TRACING=true` and
  `LANGSMITH_API_KEY` are set in the environment; we add a run name, tags and the listing
  id and detail hash as metadata, so a trace can be found by listing.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import ValidationError

from martom.extract.prompt import PROMPT_VERSION, ListingInput, build_messages
from martom.extract.schema import ExtractedProduct
from martom.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_ATTEMPTS = 3  # model calls per listing when the reply doesn't validate
HTTP_MAX_RETRIES = 3  # SDK retries per call for 429 / 5xx / network errors
MAX_TOKENS = 16_000  # thinking + the JSON reply; a long variant list fits easily
REQUEST_TIMEOUT_SECONDS = 120.0

# Errors that mean "the reply was malformed": worth asking the model again.
_RETRYABLE_REPLY_ERRORS = (ValidationError, OutputParserException)

type StructuredModel = Runnable[Sequence[BaseMessage], ExtractedProduct]


def make_structured_model(settings: Settings) -> StructuredModel:
    """The real Claude Haiku model with structured output (needs ANTHROPIC_API_KEY)."""
    if settings.anthropic_api_key is None:
        raise ValueError("ANTHROPIC_API_KEY is not set; `martom extract` needs it")
    chat = ChatAnthropic(
        model=settings.fast_model,
        api_key=settings.anthropic_api_key,
        max_tokens=MAX_TOKENS,
        max_retries=HTTP_MAX_RETRIES,
        default_request_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return chat.with_structured_output(ExtractedProduct, method="json_schema")  # type: ignore[return-value]


@dataclass(frozen=True)
class ExtractionResult:
    """One listing's outcome: a product, or why it failed after every retry."""

    listing_id: int
    product: ExtractedProduct | None
    error: str | None = None


class Extractor:
    def __init__(
        self,
        structured_model: StructuredModel,
        *,
        model_name: str,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_wait: bool = True,
    ) -> None:
        self.model_name = model_name
        self._concurrency = concurrency
        # with_retry wraps the Runnable: on a matching exception it calls it again, with
        # exponential backoff plus jitter between attempts unless retry_wait=False (tests).
        self._model = structured_model.with_retry(
            retry_if_exception_type=_RETRYABLE_REPLY_ERRORS,
            stop_after_attempt=max_attempts,
            wait_exponential_jitter=retry_wait,
        )

    async def extract(self, listing: ListingInput) -> ExtractedProduct:
        """Extract one listing; raises if it still fails after the retries."""
        config: RunnableConfig = {
            "run_name": "extract_listing",
            "tags": ["extraction", PROMPT_VERSION],
            "metadata": {
                "listing_id": listing.id,
                "detail_hash": listing.detail_hash,
                "prompt_version": PROMPT_VERSION,
                "model": self.model_name,
            },
        }
        result = await self._model.ainvoke(build_messages(listing), config=config)
        if not isinstance(result, ExtractedProduct):  # guards against a misbuilt chain
            raise TypeError(f"structured model returned {type(result).__name__}")
        return result

    async def extract_many(self, listings: Sequence[ListingInput]) -> list[ExtractionResult]:
        """Extract every listing, at most `concurrency` at a time, in input order.

        A listing that fails doesn't stop the others; its result carries the error.
        """
        limit = asyncio.Semaphore(self._concurrency)

        async def one(listing: ListingInput) -> ExtractionResult:
            async with limit:  # waits here while `concurrency` calls are running
                try:
                    product = await self.extract(listing)
                except Exception as exc:
                    logger.warning(
                        "extraction failed for listing %s",
                        listing.id,
                        exc_info=True,
                        extra={"listing_id": listing.id},
                    )
                    return ExtractionResult(listing.id, None, f"{type(exc).__name__}: {exc}")
                return ExtractionResult(listing.id, product)

        # gather runs all the coroutines concurrently and returns results in input order.
        return list(await asyncio.gather(*(one(listing) for listing in listings)))
