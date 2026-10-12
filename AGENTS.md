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
  models, statistics queries, message persistence, and ingestion.
- Use Pydantic models at tool/API boundaries, type annotations for new interfaces,
  and the repository's Ruff configuration.
- Keep `backend/pyproject.toml` and `backend/requirements.lock` consistent when dependencies change.
- Keep the README aligned with changes to setup, endpoints, schema, and behavior.

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

For database, query, or pipeline changes, also run the integration suite against
the initialized local PostgreSQL database:

```bash
NBA_TEST_DATABASE=1 .venv/bin/pytest -c backend/pyproject.toml -q
```

Frontend checks use `node --test frontend/tests/*.test.js` and
`node --check frontend/assets/app.js`. Node is only needed for development checks.

The test suite blocks paid model requests. Integration tests read NBA statistics
and write synthetic conversation logs. `backend/evals/run_live.py` is a separate, paid
OpenAI evaluation; run it when live evaluation is within the requested scope.

Add meaningful regression coverage for behavioral fixes. Documentation-only
changes need content, formatting, and link checks rather than application tests.
Report the checks performed and any material limitations. Avoid leaving a
development server running unless the user requested it.
