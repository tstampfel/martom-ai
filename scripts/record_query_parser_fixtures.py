"""Re-record the query parser's replies (M3-4). Run by hand, never in CI.

    # Tomislav only (agents never read .env). Ask claude-haiku-5-5 about every question
    # in tests/fixtures/query_parser/cases.json and save its replies to responses.json
    # (a fraction of a cent; needs ANTHROPIC_API_KEY, which uv passes from your .env):
    uv run --env-file .env python scripts/record_query_parser_fixtures.py --record

Then run `uv run pytest tests/test_query_parser.py` and review the git diff of
tests/fixtures/query_parser/: a failing case means the model reads that question
differently from what cases.json expects. Fix the prompt (and bump PROMPT_VERSION), or
the expectation if the model is right.
"""

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from martom.search.query_parser import PROMPT_VERSION, make_query_parser
from martom.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "query_parser"


async def record() -> None:
    settings = Settings()
    # Batch settings: 60 s per request and SDK retries, not live search's 1.5 s / one try.
    parser = make_query_parser(settings, for_queries=False)
    if parser is None:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set (Tomislav runs this with his local key file)"
        )
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))["cases"]
    responses = {}
    failed = []
    for case in cases:
        # parse(), not filters(): an error is reported, never turned into "no filters".
        # The questions are seed data from cases.json, not customer text, so they may be
        # printed.
        try:
            reply = await parser.parse(case["question"])
        except Exception as exc:
            print(f"FAILED {case['id']}: {case['question']!r}: {type(exc).__name__}: {exc}")
            failed.append(case["id"])
            continue
        responses[case["question"]] = reply.model_dump(mode="json")
    if failed:
        # A partial file would fail the golden test anyway; keep the old one instead.
        raise SystemExit(
            f"{len(failed)} of {len(cases)} failed ({', '.join(failed)}); "
            "responses.json not written"
        )
    payload = {
        "_about": (
            f"Replies of {settings.fast_model} for the questions in cases.json, recorded on "
            f"{date.today().isoformat()} with scripts/record_query_parser_fixtures.py --record."
        ),
        "source": "recorded",
        "model": settings.fast_model,
        "prompt_version": PROMPT_VERSION,
        "responses": responses,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    (FIXTURES / "responses.json").write_text(text + "\n", encoding="utf-8")
    print(f"recorded {len(responses)} replies to {FIXTURES / 'responses.json'}")


if __name__ == "__main__":
    arguments = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arguments.add_argument("--record", action="store_true", help="call the model (paid)")
    if not arguments.parse_args().record:
        raise SystemExit("nothing to do without --record (it calls the paid API)")
    asyncio.run(record())
