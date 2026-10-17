"""Opaque browser sessions and owner-only password + TOTP authentication."""

import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, Response

from nba_agent.config import Settings
from nba_agent.database import read_connection

VISITOR_COOKIE = "nba_visitor"
OWNER_COOKIE = "nba_owner"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=64 << 20)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64 << 20
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper(), casefold=True)
    digest = hmac.digest(key, struct.pack(">Q", counter), "sha1")
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1000000:06d}"


def verify_totp(secret: str, code: str, now: float | None = None) -> int | None:
    counter = int((time.time() if now is None else now) // 30)
    try:
        for candidate in (counter, counter - 1, counter + 1):
            if hmac.compare_digest(totp(secret, candidate), code):
                return candidate
    except (ValueError, TypeError):
        pass
    return None


def credential_version(settings: Settings) -> str:
    return hashlib.sha256(
        (
            settings.owner_password_hash.get_secret_value()
            + settings.owner_totp_secret.get_secret_value()
        ).encode()
    ).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def limited(
    request: Request, scope: str, limit: int, seconds: int, identity: str | None = None
) -> None:
    # Trust the direct peer only; forwarded headers cannot create new identities.
    peer = identity or (request.client.host if request.client else "unknown")
    key = token_hash(scope + ":" + peer)
    window = int(time.time()) // seconds
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO web_rate_limits(key,window_start,attempts) VALUES(%s,%s,1)
            ON CONFLICT(key) DO UPDATE SET window_start=EXCLUDED.window_start,
            attempts=CASE WHEN web_rate_limits.window_start=EXCLUDED.window_start
                THEN web_rate_limits.attempts+1 ELSE 1 END RETURNING attempts""",
            (key, window),
        )
        count = (await cur.fetchone())["attempts"]
    if count > limit:
        raise HTTPException(
            429, "Too many requests. Please try again later.", headers={"Retry-After": str(seconds)}
        )


async def identity(request: Request, kind: str) -> UUID | None:
    token = request.cookies.get(OWNER_COOKIE if kind == "owner" else VISITOR_COOKIE, "")
    if not token or len(token) > 128:
        return None
    async with read_connection(request.app.state.pool) as conn:
        cur = await conn.execute(
            """SELECT id,credential_version FROM browser_sessions
            WHERE token_hash=%s AND kind=%s AND expires_at>now()""",
            (token_hash(token), kind),
        )
        row = await cur.fetchone()
    if not row:
        return None
    if kind == "owner" and (
        not request.app.state.settings.owner_configured
        or row["credential_version"] != credential_version(request.app.state.settings)
    ):
        return None
    return row["id"]


async def require_owner(request: Request) -> UUID:
    owner = await identity(request, "owner")
    if owner is None:
        raise HTTPException(401, "Owner sign-in required.")
    return owner


async def require_visitor(request: Request) -> UUID:
    visitor = await identity(request, "visitor")
    if visitor is None:
        raise HTTPException(401, "Start a browser session to continue.")
    return visitor


async def issue_session(request: Request, response: Response, kind: str) -> UUID:
    token, session_id = secrets.token_urlsafe(32), uuid4()
    lifetime = timedelta(hours=8) if kind == "owner" else timedelta(days=30)
    settings = request.app.state.settings
    async with request.app.state.pool.connection() as conn:
        await conn.execute(
            "INSERT INTO browser_sessions(id,token_hash,kind,credential_version,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                session_id,
                token_hash(token),
                kind,
                credential_version(settings) if kind == "owner" else None,
                datetime.now(UTC) + lifetime,
            ),
        )
    response.set_cookie(
        OWNER_COOKIE if kind == "owner" else VISITOR_COOKIE,
        token,
        max_age=int(lifetime.total_seconds()),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )
    return session_id


async def visitor_session(request: Request, response: Response) -> UUID:
    existing = await identity(request, "visitor")
    if existing:
        return existing
    await limited(request, "visitor-create", 30, 3600)
    return await issue_session(request, response, "visitor")


async def owner_login(request: Request, response: Response, password: str, code: str) -> None:
    await limited(request, "owner-login", 5, 900)
    settings = request.app.state.settings
    await limited(request, "owner-login-global", 50, 3600, credential_version(settings))
    if not settings.owner_configured:
        raise HTTPException(
            503, "Owner access is not configured. Run nba-agent owner-setup locally."
        )
    valid = await asyncio.to_thread(
        verify_password, password, settings.owner_password_hash.get_secret_value()
    )
    counter = verify_totp(settings.owner_totp_secret.get_secret_value(), code)
    if not valid or counter is None:
        raise HTTPException(401, "Invalid password or authenticator code.")
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO web_auth_state(key,counter) VALUES(%s,%s)
            ON CONFLICT(key) DO UPDATE SET counter=EXCLUDED.counter
            WHERE web_auth_state.counter<EXCLUDED.counter RETURNING counter""",
            (credential_version(settings), counter),
        )
        if not await cur.fetchone():
            raise HTTPException(401, "Invalid password or authenticator code.")
    await issue_session(request, response, "owner")


async def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(OWNER_COOKIE, "")
    async with request.app.state.pool.connection() as conn:
        await conn.execute(
            "DELETE FROM browser_sessions WHERE token_hash=%s AND kind='owner'",
            (token_hash(token),),
        )
    response.delete_cookie(
        OWNER_COOKIE,
        path="/",
        secure=request.app.state.settings.secure_cookies,
        httponly=True,
        samesite="strict",
    )


def setup_owner(env_path: Path) -> None:
    """Interactive terminal-only enrollment; never called by the web server."""
    import getpass
    import sys

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("Run owner-setup in an interactive local terminal.")
    password = getpass.getpass("Choose an owner password (at least 12 characters): ")
    if len(password) < 12 or password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords must match and contain at least 12 characters.")
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    print("Add a time-based account named NBA Agent to your authenticator.")
    print("Local enrollment key (keep private):", secret)
    if verify_totp(secret, getpass.getpass("Enter its six-digit code to finish: ")) is None:
        raise SystemExit("Code did not match. Configuration left unchanged.")
    content = env_path.read_text() if env_path.exists() else ""
    lines = [
        line
        for line in content.splitlines()
        if not line.startswith(("OWNER_PASSWORD_HASH=", "OWNER_TOTP_SECRET="))
    ]
    lines += [f"OWNER_PASSWORD_HASH='{hash_password(password)}'", f"OWNER_TOTP_SECRET='{secret}'"]
    # Only the ignored .env receives credentials; preserve all other configuration.
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write("\n".join(lines) + "\n")
    print("Owner access configured. Restart NBA Agent, then open /admin.")
