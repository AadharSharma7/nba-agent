from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nba_agent.api import create_app
from nba_agent.config import Settings
from nba_agent.messages import Message, append_messages


@pytest.mark.integration
async def test_session_listing_uses_first_visible_user_message_and_safe_search(pool):
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
    with TestClient(create_app(Settings(openai_api_key=""))) as client:
        first = client.get("/v1/sessions", params={"q": marker, "limit": 1}).json()
        second = client.get(
            "/v1/sessions", params={"q": marker, "limit": 1, "offset": first["next_offset"]}
        ).json()
        assert first["next_offset"] == 1 and second["next_offset"] is None
        assert {first["sessions"][0]["id"], second["sessions"][0]["id"]} == {str(id) for id in ids}
        assert first["sessions"][0]["title"] == f"{marker} player 1"
        assert (
            client.get("/v1/sessions", params={"q": "'; DROP TABLE session_details; --"}).json()[
                "sessions"
            ]
            == []
        )
        assert client.get("/v1/sessions", params={"limit": 101}).status_code == 422
        assert client.get("/v1/sessions", params={"offset": -1}).status_code == 422


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
