import argparse
import asyncio
import json
import os

from nba_agent.config import BACKEND_ROOT, Settings


def main():
    parser = argparse.ArgumentParser(description="NBA prototype backend")
    parser.add_argument(
        "command", choices=["setup-env", "init-db", "ingest", "serve", "stats", "owner-setup"]
    )
    parser.add_argument("--query", help="JSON StatsQuery for stats command")
    args = parser.parse_args()
    if args.command == "setup-env":
        target = BACKEND_ROOT / ".env"
        if target.exists():
            print("backend/.env already exists; left unchanged.")
        else:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write((BACKEND_ROOT / ".example.env").read_text())
            print("Created backend/.env with placeholders. Add OPENAI_API_KEY locally for chat.")
        return
    if args.command == "owner-setup":
        from nba_agent.security import setup_owner

        setup_owner(BACKEND_ROOT / ".env")
        return
    settings = Settings()
    if args.command == "init-db":
        from nba_agent.ingestion import bootstrap

        bootstrap(settings)
    elif args.command == "ingest":
        from nba_agent.ingestion import ingest

        ingest(settings)
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("nba_agent.api:app", host=settings.host, port=settings.port)
    elif args.command == "stats":
        from nba_agent.database import make_pool
        from nba_agent.domain import StatsQuery
        from nba_agent.queries import search_nba_data

        if not args.query:
            parser.error("stats requires --query")

        async def run():
            pool = make_pool(settings)
            async with pool:
                return await search_nba_data(pool, StatsQuery.model_validate_json(args.query))

        print(json.dumps(asyncio.run(run()), indent=2))


if __name__ == "__main__":
    main()
