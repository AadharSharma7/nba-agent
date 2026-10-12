"""Opt-in evaluation of real model routing and source-backed answers. Makes paid API calls."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from nba_agent.config import Settings
from nba_agent.database import make_pool
from nba_agent.domain import ChatRequest
from nba_agent.service import ChatService


async def run(limit: int, offset: int):
    settings = Settings()
    if not settings.llm_configured:
        raise SystemExit("Add OPENAI_API_KEY to .env first. No requests were sent.")
    cases = json.loads(Path(__file__).with_name("cases.json").read_text())[offset : offset + limit]
    results = []
    async with make_pool(settings) as pool:
        service = ChatService(pool, settings)
        try:
            for case in cases:
                start = monotonic()
                result = await service.chat(ChatRequest(message=case["message"]))
                status_ok = result.status == case["expected_status"]
                actual_rows = [row for evidence in result.data for row in evidence.get("rows", [])]
                stats_ok = all(
                    any(all(row.get(k) == v for k, v in expected.items()) for row in actual_rows)
                    for expected in case.get("expected_rows", [])
                )
                expected_query = case.get("expected_query", {})
                query_ok = not expected_query or any(
                    all(
                        (set(str(p["id"]) for p in evidence["players"]) == set(value))
                        if key == "players"
                        else evidence["query"].get(key) == value
                        for key, value in expected_query.items()
                    )
                    for evidence in result.data
                )
                passed = status_ok and stats_ok and query_ok
                results.append(
                    {
                        "id": case["id"],
                        "passed": passed,
                        "status_matches": status_ok,
                        "stats_match": stats_ok,
                        "query_matches": query_ok,
                        "elapsed_seconds": round(monotonic() - start, 3),
                        "response": result.model_dump(mode="json"),
                    }
                )
                print(
                    f"{case['id']}: {'PASS' if passed else 'FAIL'} ({result.model_calls} model calls)",
                    flush=True,
                )
        finally:
            await service.close()
    output = Path("evals/results")
    output.mkdir(exist_ok=True)
    report = output / f"live-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    report.write_text(json.dumps(results, indent=2))
    print(f"Results: {report}")
    if not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    total_cases = len(json.loads(Path(__file__).with_name("cases.json").read_text()))
    parser.add_argument("--limit", type=int, default=total_cases)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.limit <= total_cases:
        parser.error(f"--limit must be between 1 and {total_cases}")
    if not 0 <= args.offset < total_cases:
        parser.error(f"--offset must be between 0 and {total_cases - 1}")
    asyncio.run(run(args.limit, args.offset))
