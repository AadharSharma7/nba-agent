import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from psycopg import Error as DatabaseError
from psycopg_pool import AsyncConnectionPool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from nba_agent import browse, dashboard, security
from nba_agent.config import REPOSITORY_ROOT, Settings
from nba_agent.database import make_pool, read_connection
from nba_agent.domain import ChatRequest, ModelMetricsPage, SessionsPage, StatsQuery
from nba_agent.queries import search_nba_data
from nba_agent.service import ChatService, MissingAPIKey, UnknownSession
from nba_agent.web_models import (
    BrowseMeta,
    DashboardFilter,
    DashboardOverview,
    DashboardPage,
    GamePage,
    LeadersPage,
    OwnerLogin,
    OwnerSession,
    Phase,
    PlayerPage,
    PlayerProfile,
    PublicMessage,
    PublicReply,
    PublicSession,
)

Owner = Annotated[UUID, Depends(security.require_owner)]
Visitor = Annotated[UUID, Depends(security.require_visitor)]


async def check_chat_budget(pool: AsyncConnectionPool, limit: float) -> None:
    async with read_connection(pool) as conn:
        cur = await conn.execute("""SELECT coalesce(sum(estimated_cost_usd),0) cost,
            count(*) FILTER (WHERE estimated_cost_usd IS NULL) unknown
            FROM model_metrics WHERE provider NOT IN ('function','test')
            AND started_at>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""")
        budget = await cur.fetchone()
    if budget["unknown"] or budget["cost"] >= limit:
        raise HTTPException(
            429, "Chat is temporarily unavailable because its daily usage limit has been reached."
        )


def create_app(settings: Settings | None = None, agents=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        pool = make_pool(settings)
        await pool.open(wait=True)
        app.state.pool = pool
        app.state.settings = settings
        app.state.chat = ChatService(pool, settings, agents)
        app.state.budget_lock = asyncio.Lock()
        try:
            yield
        finally:
            await app.state.chat.close()
            await pool.close()

    app = FastAPI(title="NBA Agent", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def browser_boundaries(request, call_next):
        origin = request.headers.get("origin")
        allowed = {f"http://localhost:{settings.port}", f"http://127.0.0.1:{settings.port}"}
        if origin and origin not in allowed:
            return JSONResponse({"detail": "Cross-origin requests are disabled."}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(
                    {"detail": "Cross-site requests are disabled."}, status_code=403
                )
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON requests are required."}, status_code=415)
            size = request.headers.get("content-length", "0")
            if (size.isdigit() and int(size) > 16384) or len(await request.body()) > 16384:
                return JSONResponse({"detail": "Request too large."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith(("/v1/", "/admin", "/health")):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/assets/"):
            # Revalidate even when a browser considers an older asset fresh.
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(DatabaseError)
    async def database_error(request, exc):
        return JSONResponse({"detail": "Database operation failed."}, status_code=503)

    @app.get("/health")
    async def health():
        async with read_connection(app.state.pool) as conn:
            cur = await conn.execute(
                "SELECT version,min_game_date,max_game_date FROM source_datasets ORDER BY imported_at DESC LIMIT 1"
            )
            source = await cur.fetchone()
        return {
            "status": "ok" if source else "needs_ingestion",
            "llm_configured": settings.llm_configured,
            "dataset": source,
        }

    @app.post("/v1/visitor")
    async def visitor(request: Request, response: Response):
        await security.visitor_session(request, response)
        return {"ready": True}

    @app.get("/v1/owner")
    async def owner_status(request: Request):
        return {
            "configured": settings.owner_configured,
            "authenticated": await security.identity(request, "owner") is not None,
        }

    @app.post("/v1/owner/login")
    async def login(body: OwnerLogin, request: Request, response: Response):
        await security.owner_login(request, response, body.password.get_secret_value(), body.code)
        return {"authenticated": True}

    @app.post("/v1/owner/logout")
    async def logout(request: Request, response: Response, owner: Owner):
        await security.logout(request, response)
        return {"authenticated": False}

    @app.get("/v1/browse", response_model=BrowseMeta)
    async def browse_metadata():
        return await browse.metadata(app.state.pool)

    @app.get("/v1/players", response_model=PlayerPage)
    async def player_search(
        q: str = Query("", max_length=100),
        limit: int = Query(12, ge=1, le=50),
        offset: int = Query(0, ge=0),
    ):
        return await browse.players(app.state.pool, q, limit, offset)

    @app.get("/v1/players/{player_id}", response_model=PlayerProfile)
    async def player_profile(player_id: int, phase: Phase = "regular"):
        return await browse.profile(app.state.pool, player_id, phase)

    @app.get("/v1/players/{player_id}/games", response_model=GamePage)
    async def player_games(
        player_id: int,
        season_start: int = Query(ge=1949, le=2100),
        phase: Phase = "regular",
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return await browse.games(app.state.pool, player_id, season_start, phase, limit, offset)

    @app.get("/v1/leaders", response_model=LeadersPage)
    async def leaders(
        season_start: int = Query(ge=1949, le=2100),
        phase: Phase = "regular",
        category: Literal["points", "rebounds", "assists"] = "points",
        minimum_games: int = Query(20, ge=1, le=82),
    ):
        return await browse.leaders(app.state.pool, season_start, phase, category, minimum_games)

    @app.post("/v1/stats")
    async def stats(query: StatsQuery):
        return await search_nba_data(app.state.pool, query)

    @app.post("/v1/chat", response_model=PublicReply)
    async def chat(body: ChatRequest, request: Request, response: Response):
        visitor_id = await security.visitor_session(request, response)
        if body.session_id:
            await owned_session(body.session_id, visitor_id)
        await security.limited(request, "chat-minute", 10, 60)
        await security.limited(request, "chat-hour", 60, 3600)
        await security.limited(
            request, "chat-day", settings.chat_daily_request_limit, 86400, "all-visitors"
        )
        # Serialize budget check and calls so concurrent visitors cannot all pass a stale subtotal.
        async with app.state.budget_lock:
            await check_chat_budget(app.state.pool, settings.chat_daily_budget_usd)
            try:
                result = await app.state.chat.chat(body, visitor_id=visitor_id)
                return PublicReply(**result.model_dump())
            except MissingAPIKey:
                raise HTTPException(
                    503, "Chat is not available yet. You can still explore player statistics."
                ) from None
            except UnknownSession:
                raise HTTPException(404, "Session not found.") from None

    async def owned_session(session_id: UUID, visitor_id: UUID):
        async with read_connection(app.state.pool) as conn:
            cur = await conn.execute(
                "SELECT id FROM session_details WHERE id=%s AND visitor_id=%s",
                (session_id, visitor_id),
            )
            if not await cur.fetchone():
                raise HTTPException(404, "Session not found.")

    @app.get("/v1/sessions", response_model=SessionsPage)
    async def list_sessions(
        visitor: Visitor,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
        q: str = Query("", max_length=100),
    ):
        async with read_connection(app.state.pool) as conn:
            cur = await conn.execute(
                """SELECT s.id,left(m.content->>'message',120) title,s.created_at,
                coalesce(s.last_activity_at,s.created_at) last_activity_at
                FROM session_details s JOIN LATERAL (SELECT content FROM message_details
                    WHERE session_id=s.id AND role='user' AND user_visible ORDER BY sequence_number LIMIT 1) m ON true
                WHERE s.visitor_id=%s AND strpos(lower(m.content->>'message'),lower(%s))>0
                ORDER BY coalesce(s.last_activity_at,s.created_at) DESC,s.id DESC LIMIT %s OFFSET %s""",
                (visitor, q.strip(), limit + 1, offset),
            )
            rows = await cur.fetchall()
        return SessionsPage(
            sessions=rows[:limit], next_offset=offset + limit if len(rows) > limit else None
        )

    @app.get("/v1/sessions/{session_id}", response_model=PublicSession)
    async def public_session(
        session_id: UUID,
        visitor: Visitor,
        after_sequence: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        await owned_session(session_id, visitor)
        async with read_connection(app.state.pool) as conn:
            cur = await conn.execute(
                """SELECT message_id,sequence_number,role,content,created_at
                FROM message_details WHERE session_id=%s AND sequence_number>%s AND user_visible
                AND (role='user' OR message_type='final_answer') ORDER BY sequence_number LIMIT %s""",
                (session_id, after_sequence, limit + 1),
            )
            rows = await cur.fetchall()
        messages = [
            PublicMessage(
                message_id=r["message_id"],
                sequence_number=r["sequence_number"],
                role="user" if r["role"] == "user" else "assistant",
                created_at=r["created_at"],
                text=r["content"].get("message", "")
                if r["role"] == "user"
                else r["content"].get("answer", ""),
                sources=r["content"].get("sources", []),
                data=r["content"].get("data", []),
            )
            for r in rows[:limit]
        ]
        return PublicSession(
            session_id=session_id,
            messages=messages,
            next_after_sequence=messages[-1].sequence_number if len(rows) > limit else None,
        )

    @app.get("/v1/admin/sessions", response_model=DashboardPage)
    async def admin_sessions(owner: Owner, filters: Annotated[DashboardFilter, Query()]):
        return await dashboard.sessions(app.state.pool, filters)

    @app.get("/v1/admin/overview", response_model=DashboardOverview)
    async def admin_overview(owner: Owner, filters: Annotated[DashboardFilter, Query()]):
        return await dashboard.overview(app.state.pool, filters)

    @app.get("/v1/admin/sessions/{session_id}", response_model=OwnerSession)
    async def admin_session(
        session_id: UUID,
        owner: Owner,
        after_sequence: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        q: str = Query("", max_length=200),
    ):
        return await dashboard.session_detail(app.state.pool, session_id, after_sequence, limit, q)

    @app.get("/v1/sessions/{session_id}/metrics", response_model=ModelMetricsPage)
    @app.get("/v1/admin/sessions/{session_id}/metrics", response_model=ModelMetricsPage)
    async def session_metrics(
        session_id: UUID,
        owner: Owner,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        return await dashboard.metrics(app.state.pool, session_id, limit, offset)

    frontend = REPOSITORY_ROOT / "frontend"
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="frontend-assets")

    def page(filename: str):
        return FileResponse(
            frontend / filename,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
            },
        )

    @app.get("/", include_in_schema=False)
    @app.get("/players", include_in_schema=False)
    @app.get("/players/{player_id}", include_in_schema=False)
    async def public_index():
        return page("index.html")

    @app.get("/admin", include_in_schema=False)
    async def owner_index():
        # This shell contains no private data; all data endpoints require owner authentication.
        return page("admin.html")

    return app


app = create_app()
