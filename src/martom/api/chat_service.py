"""The API's real advisor: models, search, pools and the checkpointed graph (#21).

Opened once per process in FastAPI's lifespan (`create_app`) and closed on shutdown:

- the Anthropic models (`make_agent_models`), the query embedder, the Haiku query parser
  and the Cohere reranker, all with their live-search settings (fast give-up);
- one search cache (`make_search_cache`, #17): lazy, so a Redis that is down only makes
  searches uncached; closed with `aclose()` on shutdown;
- a query pool for the catalogue, the BOM pricer and search (one connection per query or
  search), and a separate lock pool for the thread turn locks (api/threads.py);
- the graph with the Postgres checkpointer (`open_agent_graph`). Startup never creates
  tables or runs `setup()`: `martom migrate` does, and a database without the
  checkpointer tables stops the API here (`CheckpointerNotReadyError`).

Without ANTHROPIC_API_KEY, or without a usable embedder (EMBEDDING_PROVIDER=cohere with
no COHERE_API_KEY), there is no advisor: the service is None and every chat turn answers
`error` kind `internal`, while /health and /admin keep working. That is a warning
locally and an error in production, where the keys come from Secrets Manager.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from martom.agent.catalogue import PostgresCatalogue
from martom.agent.factory import make_agent_models, make_retriever, open_agent_graph
from martom.agent.graph import AgentDeps
from martom.agent.pricer import PostgresBomPricer
from martom.api.chat import ChatService
from martom.api.threads import PostgresThreadLocks, open_lock_pool
from martom.db import open_query_pool
from martom.embeddings import EmbeddingError, make_embedder
from martom.search.cache import make_search_cache
from martom.search.query_parser import make_query_parser
from martom.search.rerank import make_reranker
from martom.settings import Settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_chat_service(settings: Settings) -> AsyncGenerator[ChatService | None]:
    """Yield the chat service (or None without the keys); closes everything on exit."""
    models = make_agent_models(settings)
    if models is None:
        _disabled(settings, "ANTHROPIC_API_KEY is not set")
        yield None
        return
    try:
        embedder = make_embedder(settings, for_queries=True)
    except EmbeddingError:
        # The message names the setting, never a key value; the type is enough here.
        _disabled(settings, "the query embedder can't be built (EMBEDDING_PROVIDER)")
        yield None
        return
    parser = make_query_parser(settings, for_queries=True)
    reranker = make_reranker(settings, for_queries=True)
    cache = make_search_cache(settings)
    try:
        async with (
            open_query_pool(settings.database_url) as pool,
            open_lock_pool(settings.database_url) as lock_pool,
        ):
            deps = AgentDeps(
                models=models,
                retriever=make_retriever(
                    pool, embedder=embedder, parser=parser, reranker=reranker, cache=cache
                ),
                catalogue=PostgresCatalogue(pool),
                pricer=PostgresBomPricer(pool),
            )
            async with open_agent_graph(deps, settings.database_url) as graph:
                logger.info(
                    "chat service ready",
                    extra={
                        "parser": parser is not None,
                        "reranker": reranker is not None,
                        "search_cache": cache is not None,
                    },
                )
                yield ChatService(graph=graph, locks=PostgresThreadLocks(lock_pool))
    finally:
        if cache is not None:
            await cache.aclose()


def _disabled(settings: Settings, reason: str) -> None:
    level = logging.ERROR if settings.app_env == "production" else logging.WARNING
    logger.log(level, "chat disabled", extra={"reason": reason})
