CREATE TABLE source_datasets (
    id text PRIMARY KEY,
    version integer NOT NULL,
    source_url text NOT NULL,
    archive_sha256 text NOT NULL,
    source_updated_at timestamptz,
    imported_at timestamptz NOT NULL DEFAULT now(),
    min_game_date date,
    max_game_date date,
    counts jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE players (
    id bigint PRIMARY KEY,
    name text NOT NULL,
    normalized_name text NOT NULL,
    normalized_surname text NOT NULL
);
CREATE INDEX players_name_idx ON players(normalized_name);
CREATE INDEX players_surname_idx ON players(normalized_surname);

CREATE TABLE teams (
    id bigint PRIMARY KEY,
    name text NOT NULL
);
CREATE TABLE team_aliases (
    team_id bigint REFERENCES teams(id),
    alias text NOT NULL,
    PRIMARY KEY(alias, team_id)
);
CREATE INDEX team_aliases_team_idx ON team_aliases(team_id);

CREATE TABLE games (
    id text PRIMARY KEY,
    source_id text NOT NULL REFERENCES source_datasets(id),
    season_start smallint NOT NULL CHECK(season_start >= 1949),
    phase text NOT NULL CHECK(phase IN ('regular','playoffs')),
    played_at timestamp NOT NULL,
    home_team_id bigint NOT NULL REFERENCES teams(id),
    away_team_id bigint NOT NULL REFERENCES teams(id),
    home_team_name text NOT NULL,
    away_team_name text NOT NULL,
    home_score integer,
    away_score integer,
    playoff_round text,
    conference text,
    series_game_number smallint,
    source_label text
);
COMMENT ON COLUMN games.played_at IS 'Source gameDateTimeEst wall time in America/New_York; not UTC.';
CREATE INDEX games_scope_idx ON games(season_start, phase, playoff_round);
CREATE INDEX games_date_idx ON games(played_at);
CREATE INDEX games_home_idx ON games(home_team_id, played_at);
CREATE INDEX games_away_idx ON games(away_team_id, played_at);
CREATE INDEX games_source_idx ON games(source_id);

CREATE TABLE player_game_stats (
    player_id bigint NOT NULL REFERENCES players(id),
    game_id text NOT NULL REFERENCES games(id),
    team_id bigint NOT NULL REFERENCES teams(id),
    opponent_team_id bigint NOT NULL REFERENCES teams(id),
    home boolean NOT NULL,
    appeared boolean NOT NULL,
    minutes double precision,
    points integer, rebounds integer, assists integer, steals integer, blocks integer,
    turnovers integer, fgm integer, fga integer, fg3m integer, fg3a integer,
    ftm integer, fta integer,
    source_comment text,
    PRIMARY KEY(player_id, game_id)
);
CREATE INDEX player_game_game_idx ON player_game_stats(game_id);
CREATE INDEX player_game_team_idx ON player_game_stats(team_id, game_id);
CREATE INDEX player_game_opponent_idx ON player_game_stats(opponent_team_id, game_id);

CREATE TABLE team_game_stats (
    team_id bigint REFERENCES teams(id),
    game_id text REFERENCES games(id),
    points integer, rebounds integer, assists integer,
    PRIMARY KEY(team_id, game_id)
);
CREATE INDEX team_game_game_idx ON team_game_stats(game_id);

CREATE TABLE session_details (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE chat_turns (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES session_details(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    user_message text NOT NULL,
    status text NOT NULL DEFAULT 'running',
    response jsonb,
    gate_decision jsonb,
    tool_calls jsonb NOT NULL DEFAULT '[]',
    usage jsonb NOT NULL DEFAULT '{}',
    error_code text,
    elapsed_ms integer
);
CREATE INDEX chat_turns_session_idx ON chat_turns(session_id, created_at);
