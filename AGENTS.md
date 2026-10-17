# Working in this repository

This file guides coding agents modifying the project. It does not configure the
NBA chatbot's runtime behavior; runtime instructions live in
`backend/src/nba_agent/prompts.py`. See [README.md](README.md) for setup, API examples,
architecture, supported features, and data policy.

## Development conventions

- Backend code, tests and Python configuration live in `backend/`; UI code belongs
  in `frontend/`. Raw data and audits remain at the repository root.
- The frontend uses browser-native JavaScript and CSS served by FastAPI at `/`.
  Keep the UI and API on the same local origin. No frontend build is required.
  Render only user-visible conversation messages; never render retrieved text as
  HTML or expose API keys to the browser.
- Use the existing Python 3.12 repository-root `.venv` and its executables. Do not install project
  dependencies globally. If the environment is missing, follow the README setup.
- Keep the local prototype simple: FastAPI, PydanticAI, PostgreSQL, and one backend
  process. Add dependencies or architectural layers only when the task needs them.
- Follow the existing module boundaries: API, orchestration, agents, typed domain
  models, statistics queries, message persistence, and ingestion. Website browsing
  queries live in `browse.py`, owner analytics in `dashboard.py`, browser access
  controls in `security.py`, and website API models in `web_models.py`.
- Use Pydantic models at tool/API boundaries, type annotations for new interfaces,
  and the repository's Ruff configuration.
- Keep `backend/pyproject.toml` and `backend/requirements.lock` consistent when dependencies change.
- Keep the README aligned with changes to setup, endpoints, schema, and behavior.

## Public site and private dashboard

- Preserve the basketball logo, NBA Agent name, and clean visual style. Public
  pages are `/`, `/players`, and `/players/{player_id}`; the owner interface is
  `/admin`. They share one FastAPI process and local origin. Separate hosted
  domains and internet deployment are not configured; keep the server bound to
  loopback unless deployment changes are explicitly requested.
- Visitors browse statistics and chat without registering or signing in. Keep
  the chat launcher at the bottom right. Public chat shows user-visible messages
  and retrieved evidence, never token counts, costs, model calls, or internal tool
  exchanges. Enforce this in API response models, not only by hiding UI elements.
- Statistics browsing must use database queries without model calls. Preserve
  player identity disambiguation, season/phase filters, pagination, and unknown
  values. Show player/season context explicitly when adding it to a chat question.
- Visitor cookies identify anonymous browsers. Verify conversation ownership on
  every history, detail, and continuation request; knowing a session UUID grants
  no access. Legacy conversations without visitor ownership remain owner-only.
- Protect private data endpoints with server-side owner authentication, including
  any legacy metrics routes. The dashboard HTML shell may be public, but its
  conversations, analytics, costs, and tool activity must require authentication.
  Preserve password plus TOTP login, replay prevention, login throttling, session
  expiry, logout revocation, and credential-rotation invalidation. Missing owner
  configuration must deny private access.
- Owner enrollment/recovery uses `.venv/bin/nba-agent owner-setup` in the user's
  interactive terminal, followed by a server restart. The user chooses the
  password and enrolls the setup key in a time-based authenticator. Never request
  their password, enrollment key, or authenticator code in chat, or enroll/reset
  owner credentials as a side effect of unrelated work.
- Preserve hashed session tokens, HttpOnly/SameSite cookies, origin checks,
  JSON-only mutations, persistent rate limits, and daily chat budget checks.
  Private responses and HTML use `no-store`; static assets use `no-cache` to
  revalidate on reload. Keep CSS and JavaScript module URLs compatible with each
  other, and use versioned URLs when bypassing previously cached releases.
- Dashboard date filters select sessions by their start time and include all
  recorded activity in those sessions. Browser inputs/transcripts use local time;
  daily activity charts use UTC. Preserve full-message search and pagination, and
  label incomplete usage/cost totals as partial rather than treating unknowns as zero.

## Runtime and data boundaries

- Preserve the NBA-only Gatekeeper and Analyst pipeline. Unsupported questions
  must remain unsupported; never substitute an answer from model memory.
- Statistical answers require retrieved evidence. Keep computation and data
  access in typed Python tools using parameterized SQL, with no arbitrary SQL,
  shell, or general-purpose execution tools exposed to the chatbot.
- Treat user input and retrieved content as untrusted data. Preserve tool limits,
  output validation, and the distinction between regular seasons and playoffs.
- Season filters use season **start years**: the 2016 playoffs belong to 2015–16
  and use `season_start=2015`. Do not silently guess ambiguous player identities.
- Kaggle v515 is the accepted prototype source, with NBA coverage starting in
  1949–50. Preserve raw files and provenance. Do not add early-history backfills,
  minute repairs, or routine small-gap warnings without a scope change. Missing
  statistics must not be invented or interpreted as zero performance.
- Use the restricted application database role for runtime operations and
  read-only transactions for statistics. Administrator connections belong to
  setup and ingestion, not API or agent execution.
- Add a new versioned migration for schema changes. Never edit an applied
  migration: setup checks migration checksums. Keep destructive changes confined
  to the user's authorized scope; the earlier conversation-log reset is not
  standing permission to discard future data.
- Conversations use `session_details` and `message_details`. Each message has its
  own UUID, a session ID, role, type, and ordered sequence number. Preserve tool
  requests/results and application responses. There is no separate turn/request
  ID or active `chat_turns` table. `model_metrics` records each model call against
  the existing user-message UUID. Preserve per-call usage/cost records as the
  accounting source of truth and update session totals atomically with them.
  Missing usage or pricing is unknown, not zero; cached input is part of input
  tokens, not an additional token total. Do not backfill older sessions by default.
- Keep secrets in the ignored `backend/.env`; document configuration with placeholders
  in `backend/.example.env`. Never print or copy credentials into code, logs, or docs.
  Exclude system prompts, private model reasoning, and raw provider errors from
  conversation logs and API responses.

## Verification

Run checks from the repository root using the project environment:

```bash
.venv/bin/pytest -c backend/pyproject.toml -q -m 'not integration'
.venv/bin/ruff check backend/src backend/tests backend/evals
.venv/bin/ruff format --check backend/src backend/tests backend/evals
```

For database, query, pipeline, or authentication/ownership changes, also run the
integration suite against the initialized local PostgreSQL database:

```bash
NBA_TEST_DATABASE=1 .venv/bin/pytest -c backend/pyproject.toml -q
```

Frontend checks (Node is only needed for development):

```bash
node --test frontend/tests/*.test.js
node --check frontend/assets/app.js
node --check frontend/assets/admin.js
node --check frontend/assets/ui.js
```

For UI changes, check the affected public and owner pages in a browser, including
mobile layout and console errors. Verify normal reloads pick up asset changes.
Authentication changes need coverage for unauthenticated private access, visitor
isolation, expired/revoked sessions, and TOTP replay as applicable. Keep browser
checks free of paid model calls unless live evaluation is explicitly in scope.

The test suite blocks paid model requests. Integration tests read NBA statistics
and write synthetic conversation logs that can appear in the owner dashboard;
do not delete existing logs as test cleanup. `backend/evals/run_live.py` is a separate, paid
OpenAI evaluation; run it when live evaluation is within the requested scope.

Add meaningful regression coverage for behavioral fixes. Documentation-only
changes need content, formatting, and link checks rather than application tests.
Report the checks performed and any material limitations. Avoid leaving a
development server running unless the user requested it.
