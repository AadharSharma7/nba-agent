# NBA Agent — local backend prototype

Python 3.12, FastAPI, PydanticAI, OpenAI and PostgreSQL. The fixed Kaggle v515
snapshot is the sole statistical source. No vector database, frontend, live-data
provider or document retrieval is needed for this first statistics backend.

## Current local setup

The project virtual environment is `.venv`. PostgreSQL 18.6 runs through
Postgres.app on localhost:5432. The backend uses the dedicated `nba_agent`
database and restricted `nba_agent_app` role. Its administrator connection is
used only by setup/import commands, never by the API or agents.

The import contains 71,232 games, 1,604,191 player/game rows and 142,464 team/game
rows. Historical records begin in 1949–50. Raw preseason, BAA-era, All-Star,
play-in and Cup final games are excluded. Regular-season Cup games are included.
The extended-stat and play-by-play files remain in raw storage for future work.

Start the backend from this directory:

```bash
.venv/bin/nba-agent serve
```

Open [the interactive API docs](http://127.0.0.1:8001/docs) to try requests.
Use [the health endpoint](http://127.0.0.1:8001/health) to inspect database and model readiness.

Add your OpenAI key to `.env` and restart the backend to enable chat.
The placeholder is deliberately recognized as unconfigured, so it is never sent
to OpenAI. Statistics endpoints work without an API key. The configurable
default model is `gpt-4.1-mini` for both agents; no key is stored in session logs.

The API is for one trusted local user. Keep it bound to 127.0.0.1. It has no user
authentication and is not a public deployment. Session IDs are not authentication.
Cross-origin browser requests and unexpected Host headers are rejected.

## Try the data API without a model

From the interactive docs, POST to `/v1/stats` with:

```json
{
  "players": ["Stephen Curry"],
  "season_start": 2015,
  "season_end": 2015,
  "phase": "playoffs",
  "playoff_round": "conference_finals",
  "conference": "west"
}
```

Or use the command line:

```bash
.venv/bin/nba-agent stats --query '{"players":["Curry"],"season_start":2015,"season_end":2015,"phase":"playoffs","playoff_round":"conference_finals","conference":"west"}'
```

Expected: seven games, 195 points, 44 rebounds and 41 assists. Minutes retain the
Kaggle values. Shooting percentages use total makes divided by total attempts.

Other supported queries:

```json
{"players":["Nikola Jokic","Joel Embiid"],"season_start":2020,"phase":"playoffs"}
```

```json
{"players":["Kawhi Leonard"],"opposing_player":"LeBron James","phase":"regular"}
```

```json
{"players":["Curry"],"last_n":5,"group_by":"game"}
```

`season_start` and `season_end` are inclusive START years. Set both for a
single season; 2020 means 2020–21, including the playoffs held in 2021. `last_n`
is applied per player after other filters. Default competition is regular season.
Game logs have an explicit result limit and a `truncated` flag. Comparisons can
contain up to four players. Surname ambiguity produces candidates, not guesses;
common aliases such as Curry, Steph and LeBron are explicit mappings.

## Chat and session inspection

POST to `/v1/chat`:

```json
{"message":"What were Curry's stats in the 2016 Western Conference Finals?"}
```

The response contains a session ID. Supply it on subsequent messages:

```json
{"message":"And his assists?","session_id":"<returned-session-id>"}
```

Each chat response includes `session_id` and `message_id`. The latter is the
UUID of the final response row; the old `turn_id` field has been removed.

`session_details` holds conversations. `message_details` holds individual
messages with unique UUID primary keys, a session ID, sequence number, role,
message type, JSON content and timestamp. Roles are `user`, `gate`,
`analyst`, `tool` and `application`. Tool-call IDs connect requests with
results; there is no separate turn/request ID or `chat_turns` table.

GET `/v1/sessions/<session-id>` returns `session` and ordered `messages`.
The default page is 100 messages (maximum 500). Pass `limit` and
`after_sequence` to paginate; `next_after_sequence` is null on the last page.
In pgAdmin, sort `message_details` by `session_id, sequence_number`.

Messages include gate decisions, analyst outputs, tool arguments/results,
validation feedback, failed tool calls and application-generated replies.
`user_visible` distinguishes chat content from internal exchanges. The final
message contains the API response; its metadata holds total model usage, elapsed
time and any error type. Model usage is also attached to each model response
(once when a response has several parts). These records exclude system prompts,
private model reasoning and raw provider errors. The user message is committed
before processing; agent exchanges are committed when request processing finishes.
The most recent four completed exchanges provide follow-up context.
Concurrent requests within a session are serialized by the single local worker.
The source version, cutoff and actual computed rows are attached to answers.
Default refusal, unsupported and unresolved-entity clarification messages are
application-owned. Ambiguous names stop the model loop immediately.

## Pipeline and scope

The Gatekeeper makes a structured domain decision. It has no tools. The Analyst
then decides whether to call the typed statistics tool, clarify, or return an
unsupported result. Python runs fixed, parameterized SQL and returns evidence.
The Analyst receives that data and writes the human-readable answer. A typical
successful request makes three model calls (one Gatekeeper, two Analyst).

Explicit instruction attacks can be refused without a model call. Known unsupported
statistical filters are stopped after the Gatekeeper. Ambiguous surname expansions
are checked against the database, and explicit playoff calendar years are checked
at the tool boundary. Other inputs
go through the LLM gate. The Analyst has no shell, filesystem, web access or
arbitrary SQL tool. Database permissions prevent changes to imported NBA data;
statistical queries also run inside read-only transactions. Request, tool-call,
token, timeout and output-length limits bound work. Statistical answers must cite
evidence returned in the current turn; a numerical consistency check rejects
unsupported numbers. Validation retries count toward the model-call limit.

These controls reduce prompt-injection and hallucination risk; they do not prove
that an LLM will classify every adversarial request correctly or interpret every
query perfectly. Offline mock-model tests verify orchestration, not real-model
judgment. Live evaluations are separate and explicitly opt-in.

This version supports player/season/game/series statistics, comparisons, opponent
filters and opposing-player shared appearances. It does NOT yet support NBA
rulebook/history retrieval, live schedules, standings, elimination-game logic,
Game 7 filtering, injuries/roster-based absence analysis, team analytics or
advanced-rating queries. Unsupported NBA questions do not fall back to model
memory. Missing rows are not interpreted as zero statistics.

## Prototype data policy

Kaggle values and historical gaps are accepted as requested. There is no early
history backfill, minute repair or routine gap warning in generated answers.
Original files and source provenance are retained. Basic import normalization
preserves text IDs, assigns seasons/phases and maps player teams using game IDs
and home/away. Two extraneous team-ID-0 rows cannot satisfy relational integrity
and are skipped, with their count recorded in source_datasets. Source comments
are stored but are not passed to the model. Zero recorded minutes do not by
themselves establish DNP; positive box-score activity takes precedence.

## Reproduce the environment and database

Run from the project root. Existing .env files are preserved:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/nba-agent setup-env
.venv/bin/nba-agent init-db
.venv/bin/nba-agent ingest
```

`requirements.lock` pins the full tested environment, including audit/testing
dependencies. Direct application dependencies are also pinned in pyproject.toml.
The current .env database password is a local-only prototype value; change it
before using this configuration beyond your private machine.

The importer verifies source-file hashes against audit/reports/snapshot_manifest.json,
loads inside one transaction, and records the archive checksum and counts. Repeating
the same import is a no-op. It refuses a conflicting snapshot checksum. Database
migrations are versioned and checksummed; modify an applied schema through a new
migration rather than editing its history. No default databases are overwritten.
Migration 002 replaces `chat_turns` with `message_details` and discards prior
prototype conversation logs, as agreed. It leaves all NBA statistics intact.
Stop any running backend before applying it with `nba-agent init-db`, then
restart the backend. Repeating `init-db` does not clear messages again.

## Tests and evaluations

Offline tests never make paid model requests:

```bash
.venv/bin/pytest -q -m 'not integration'
NBA_TEST_DATABASE=1 .venv/bin/pytest -q
.venv/bin/ruff check src tests evals
```

Integration tests require the imported local database. They read NBA records and
write synthetic session logs, but never mutate the imported dataset. Tests cover
priority query totals, weighted percentages, participation, name ambiguity,
parameterized injection attempts, least-privilege access, refusals, evidence
validation, follow-up sessions and HTTP boundaries.

After adding a real key, explicitly run the live evaluation suite (paid calls):

```bash
.venv/bin/python evals/run_live.py --limit 3
.venv/bin/python evals/run_live.py
```

Results record routing status, expected statistical values, latency and model
calls. Review the generated prose too: aggregate matches do not prove every
sentence is semantically correct. Model errors are retained by type in session
logs; provider error messages and credentials are not exposed through the API.

## Project layout

Coding-agent conventions are documented in [AGENTS.md](AGENTS.md).

* src/nba_agent/api.py: HTTP interface and local request boundaries.
* src/nba_agent/agents.py: Gatekeeper, Analyst, tool and output validation.
* src/nba_agent/prompts.py: Gatekeeper and Analyst runtime instructions.
* src/nba_agent/service.py: orchestration and conversation flow.
* src/nba_agent/messages.py: ordered message persistence and conversation context.
* src/nba_agent/queries.py: entity resolution and fixed analytical SQL.
* src/nba_agent/domain.py: validated request/response models.
* src/nba_agent/ingestion.py and migrations/: local setup and source ingestion.
* tests/: unit, database and mock-model integration tests.
* evals/: real-model evaluation cases and opt-in runner.
* audit/: original data audit; its recommendations predate the accepted prototype policy.
* data/raw/: unchanged, ignored Kaggle snapshot and archive.

## References

- [Kaggle dataset](https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores)
- [PydanticAI agents](https://ai.pydantic.dev/agents/)
- [PydanticAI testing](https://ai.pydantic.dev/testing/)
- [OpenAI model configuration](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
