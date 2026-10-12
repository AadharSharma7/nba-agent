from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from psycopg import Error as DatabaseError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from nba_agent.config import Settings
from nba_agent.database import make_pool, read_connection
from nba_agent.domain import ChatRequest, ChatResponse, ModelMetricsPage, StatsQuery
from nba_agent.queries import search_nba_data
from nba_agent.service import ChatService, MissingAPIKey, UnknownSession


def create_app(settings: Settings | None = None, agents=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        pool = make_pool(settings)
        await pool.open(wait=True)
        app.state.pool = pool
        app.state.chat = ChatService(pool, settings, agents)
        try:
            yield
        finally:
            await app.state.chat.close()
            await pool.close()

    app = FastAPI(title="NBA Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def local_browser_only(request, call_next):
        # No CORS allowance. Also reject cross-site POSTs to the local personal API.
        origin = request.headers.get("origin")
        allowed = {f"http://localhost:{settings.port}", f"http://127.0.0.1:{settings.port}"}
        if origin and origin not in allowed:
            return JSONResponse({"detail": "Cross-origin requests are disabled."}, status_code=403)
        size = request.headers.get("content-length", "0")
        if size.isdigit() and int(size) > 16384:
            return JSONResponse({"detail": "Request too large."}, status_code=413)
        return await call_next(request)

    @app.exception_handler(DatabaseError)
    async def database_error(request, exc):
        return JSONResponse({"detail": "Database operation failed."}, status_code=503)

    @app.get("/health")
    async def health():
        async with read_connection(app.state.pool) as conn:
            cur = await conn.execute(
                "SELECT version, min_game_date, max_game_date, counts FROM source_datasets ORDER BY imported_at DESC LIMIT 1"
            )
            source = await cur.fetchone()
        return {
            "status": "ok" if source else "needs_ingestion",
            "llm_configured": settings.llm_configured,
            "dataset": source,
        }

    @app.post("/v1/stats")
    async def stats(query: StatsQuery):
        return await search_nba_data(app.state.pool, query)

    @app.post("/v1/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest):
        try:
            return await app.state.chat.chat(request)
        except MissingAPIKey:
            raise HTTPException(
                503, "Set OPENAI_API_KEY in backend/.env and restart the backend to enable chat."
            ) from None
        except UnknownSession:
            raise HTTPException(404, "Session not found.") from None

    @app.get("/v1/sessions/{session_id}")
    async def session_details(
        session_id: UUID,
        after_sequence: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        async with read_connection(app.state.pool) as conn:
            cur = await conn.execute("SELECT * FROM session_details WHERE id=%s", (session_id,))
            session = await cur.fetchone()
            if not session:
                raise HTTPException(404, "Session not found.")
            if session["estimated_cost_usd"] is not None:
                session["estimated_cost_usd"] = str(session["estimated_cost_usd"])
            cur = await conn.execute(
                "SELECT * FROM message_details WHERE session_id=%s AND sequence_number>%s ORDER BY sequence_number LIMIT %s",
                (session_id, after_sequence, limit + 1),
            )
            rows = await cur.fetchall()
            more = len(rows) > limit
            messages = rows[:limit]
            return {
                "session": session,
                "messages": messages,
                "next_after_sequence": messages[-1]["sequence_number"] if more else None,
            }

    @app.get("/v1/sessions/{session_id}/metrics", response_model=ModelMetricsPage)
    async def session_metrics(
        session_id: UUID,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> ModelMetricsPage:
        async with app.state.pool.connection() as conn:
            cur = await conn.execute("SELECT id FROM session_details WHERE id=%s", (session_id,))
            if not await cur.fetchone():
                raise HTTPException(404, "Session not found.")
            cur = await conn.execute(
                """SELECT * FROM model_metrics WHERE session_id=%s
                ORDER BY started_at,id LIMIT %s OFFSET %s""",
                (session_id, limit + 1, offset),
            )
            rows = await cur.fetchall()
        return ModelMetricsPage(
            session_id=session_id,
            metrics=rows[:limit],
            next_offset=offset + limit if len(rows) > limit else None,
        )

    return app


app = create_app()
