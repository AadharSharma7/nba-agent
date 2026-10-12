import pytest
from psycopg.errors import InsufficientPrivilege, ReadOnlySqlTransaction

from nba_agent.database import read_connection
from nba_agent.domain import StatsQuery
from nba_agent.queries import ambiguous_expansion, search_nba_data

pytestmark = pytest.mark.integration


async def test_curry_wcf(pool):
    result = await search_nba_data(
        pool,
        StatsQuery(
            players=["Curry"],
            season_start=2015,
            season_end=2015,
            phase="playoffs",
            playoff_round="conference_finals",
            conference="west",
        ),
    )
    row = result["rows"][0]
    assert (row["games"], row["points"], row["rebounds"], row["assists"]) == (7, 195, 44, 41)
    assert row["ppg"] == pytest.approx(195 / 7)
    assert row["fg_pct"] == pytest.approx(100 * 62 / 140)
    assert row["mpg"] == pytest.approx(254 / 7)  # Accepted source precision, not repaired.
    assert result["source"]["version"] == 515


async def test_comparison_since_2021(pool):
    result = await search_nba_data(
        pool,
        StatsQuery(players=["Nikola Jokić", "Joel Embiid"], season_start=2020, phase="playoffs"),
    )
    rows = {r["player_id"]: r for r in result["rows"]}
    assert (rows[203999]["games"], rows[203999]["points"]) == (67, 1919)
    assert (rows[203954]["games"], rows[203954]["points"]) == (43, 1124)


async def test_both_players_on_opposing_teams(pool):
    result = await search_nba_data(
        pool, StatsQuery(players=["Kawhi"], opposing_player="LeBron", phase="regular")
    )
    row = result["rows"][0]
    assert (row["games"], row["points"], row["rebounds"], row["assists"]) == (28, 673, 182, 97)
    result = await search_nba_data(
        pool, StatsQuery(players=["Kawhi"], opposing_player="LeBron", phase="playoffs")
    )
    assert result["rows"][0]["games"] == 12


async def test_last_n_and_game_log(pool):
    common = dict(
        players=["Stephen Curry"],
        season_start=2015,
        season_end=2015,
        phase="playoffs",
        opponent_team="OKC",
        last_n=3,
    )
    games = await search_nba_data(pool, StatsQuery(**common, group_by="game"))
    total = await search_nba_data(pool, StatsQuery(**common))
    assert len(games["rows"]) == 3
    assert total["rows"][0]["games"] == 3
    assert total["rows"][0]["points"] == sum(g["points"] for g in games["rows"]) == 98
    assert games["rows"][0]["game_date"] == "2016-05-30"


async def test_clarification_injection_and_no_data(pool):
    ambiguous = await search_nba_data(pool, StatsQuery(players=["James"]))
    assert ambiguous["status"] == "clarification" and len(ambiguous["candidates"]) > 1
    attack = await search_nba_data(pool, StatsQuery(players=["'; DROP TABLE players; --"]))
    assert attack["status"] == "not_found"
    empty = await search_nba_data(pool, StatsQuery(players=["Curry"], season_start=2050))
    assert empty["status"] == "no_data" and empty["rows"] == []


async def test_database_permissions(pool):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
        )
        assert not any((await cur.fetchone()).values())
    with pytest.raises(InsufficientPrivilege):
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM players WHERE id=-1")
    with pytest.raises(ReadOnlySqlTransaction):
        async with read_connection(pool) as conn:
            await conn.execute("INSERT INTO session_details(id) VALUES(gen_random_uuid())")


async def test_model_cannot_guess_an_ambiguous_surname(pool):
    guessed = StatsQuery(players=["LeBron James"])
    result = await ambiguous_expansion(pool, guessed, "What are James's stats?")
    assert result and result["status"] == "clarification"
    assert await ambiguous_expansion(pool, guessed, "What are LeBron's stats?") is None
    assert (
        await ambiguous_expansion(
            pool, guessed, "What are James's stats?", "We are discussing LeBron James."
        )
        is None
    )


@pytest.mark.parametrize("name", ["Luka Doncic", "Luka Dončić", "Doncic"])
async def test_luka_duplicate_biographies_resolve_to_nba_identity(pool, name):
    result = await search_nba_data(
        pool, StatsQuery(players=[name], season_start=2023, season_end=2023)
    )
    assert result["status"] == "ok"
    row = result["rows"][0]
    assert row["player_id"] == 1629029
    assert row["games"] == 70
    assert row["points"] == 2370
