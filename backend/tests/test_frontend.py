import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nba_agent.api import create_app
from nba_agent.config import Settings
from nba_agent.messages import Message, append_messages


@pytest.mark.parametrize("route", ["/", "/players", "/players/201939", "/admin"])
def test_frontend_assets_bypass_old_cache_and_revalidate(route):
    # No lifespan or database needed to serve the HTML and static assets.
    client = TestClient(create_app(Settings(openai_api_key="")))
    page = client.get(route)
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    urls = re.findall(r'(?:href|src)="(/assets/[^\"]+\.(?:css|js)[^\"]*)"', page.text)
    assert len(urls) == 2
    for url in urls:
        assert "?v=" in url
        asset = client.get(url)
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"
        cached = client.get(url, headers={"If-None-Match": asset.headers["etag"]})
        assert cached.status_code == 304
        assert cached.headers["cache-control"] == "no-cache"
        if ".js?" in url:
            imports = re.findall(r'from "(\./[^\"]+)"', asset.text)
            assert imports
            for module in imports:
                assert "?v=" in module
                assert client.get("/assets/" + module.removeprefix("./")).status_code == 200


@pytest.mark.integration
async def test_session_listing_uses_first_visible_user_message_and_safe_search(pool, owner_access):
    marker = f"ui-list-{uuid4()}"
    ids = [uuid4(), uuid4()]
    for index, session_id in enumerate(ids):
        async with pool.connection() as conn:
            await conn.execute("INSERT INTO session_details(id) VALUES(%s)", (session_id,))
        await append_messages(
            pool,
            session_id,
            [
                Message(
                    "user",
                    "user_message",
                    {"message": f"{marker} player {index}"},
                    user_visible=True,
                ),
                Message(
                    "application",
                    "final_answer",
                    {"answer": "Do not use as title"},
                    user_visible=True,
                ),
            ],
        )
    with TestClient(owner_access(create_app(Settings(openai_api_key="")))) as client:
        first = client.get("/v1/admin/sessions", params={"q": marker, "limit": 1}).json()
        second = client.get(
            "/v1/admin/sessions", params={"q": marker, "limit": 1, "offset": first["next_offset"]}
        ).json()
        assert first["next_offset"] == 1 and second["next_offset"] is None
        assert {first["sessions"][0]["id"], second["sessions"][0]["id"]} == {str(id) for id in ids}
        assert first["sessions"][0]["title"] == f"{marker} player 1"
        assert (
            client.get(
                "/v1/admin/sessions", params={"q": "'; DROP TABLE session_details; --"}
            ).json()["sessions"]
            == []
        )
        assert client.get("/v1/admin/sessions", params={"limit": 101}).status_code == 422
        assert client.get("/v1/admin/sessions", params={"offset": -1}).status_code == 422


@pytest.mark.integration
def test_frontend_is_same_origin_and_does_not_expose_repository_files():
    with TestClient(create_app(Settings(openai_api_key=""))) as client:
        page = client.get("/")
        assert page.status_code == 200 and "NBA Agent" in page.text
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/styles.css").status_code == 200
        assert client.get("/backend/.env").status_code == 404
        assert client.get("/assets/%2e%2e/%2e%2e/backend/.env").status_code == 404
        assert client.get("/health").json()["llm_configured"] is False
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
        assert (
            client.post(
                "/v1/chat", json={"message": "NBA stats"}, headers={"Origin": "http://evil.example"}
            ).status_code
            == 403
        )
