import {
  el,
  button,
  request,
  table,
  seasonLabel,
  phaseLabel,
  debounce,
  sortRows,
} from "./ui.js?v=2";
import { stat, safeSource, scope } from "./format.js?v=2";
const $ = (id) => document.getElementById(id);
const state = {
  meta: null,
  profile: null,
  phase: "regular",
  season: null,
  unit: "average",
  view: "seasons",
  sort: "season_start",
  direction: -1,
  category: "points",
  games: [],
  gameOffset: null,
  directory: [],
  directoryOffset: null,
  history: [],
  historyOffset: null,
  session: null,
  busy: false,
  context: null,
};
let routeVersion = 0,
  leaderVersion = 0,
  searchVersion = 0,
  directoryVersion = 0;
try {
  state.session = localStorage.getItem("nba-session");
} catch {
  /* Storage may be disabled. */
}
const report = (error) => {
  $("notice").textContent = error.message;
  $("notice").hidden = false;
};
function playerLink(player) {
  const link = el("a", player.name);
  link.href = `/players/${player.id}`;
  link.dataset.route = "";
  return link;
}
function navigate(path) {
  history.pushState(null, "", path);
  route().catch(report);
  window.scrollTo({ top: 0, behavior: "instant" });
}
document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-route]");
  if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
    event.preventDefault();
    navigate(link.getAttribute("href"));
  }
});
window.addEventListener("popstate", () => route().catch(report));
function setChoices(selector, key, value) {
  document
    .querySelectorAll(`[${selector}]`)
    .forEach((node) =>
      node.setAttribute("aria-pressed", String(node.dataset[key] === value)),
    );
}
async function route() {
  const version = ++routeVersion;
  $("notice").hidden = true;
  const match = location.pathname.match(/^\/players\/(\d+)$/),
    directory = location.pathname === "/players";
  $("home").hidden = Boolean(match) || directory;
  $("directory").hidden = !directory;
  $("player").hidden = !match;
  if (match) {
    const params = new URLSearchParams(location.search);
    state.phase = params.get("phase") === "playoffs" ? "playoffs" : "regular";
    state.season = Number(params.get("season")) || null;
    $("player-name").textContent = "Loading player…";
    $("summary").replaceChildren();
    $("player-stats").replaceChildren();
    $("ask-player").disabled = true;
    $("ask-season").disabled = true;
    const profile = await request(
      `/v1/players/${match[1]}?phase=${state.phase}`,
    );
    if (version !== routeVersion) return;
    state.profile = profile;
    if (!profile.seasons.some((row) => row.season_start === state.season))
      state.season = profile.seasons[0]?.season_start ?? null;
    state.view = "seasons";
    state.games = [];
    state.sort = "season_start";
    state.direction = -1;
    document.title = `${profile.player.name} · NBA Agent`;
    $("player-name").textContent = profile.player.name;
    $("player-crumb").textContent = profile.player.name;
    $("season").replaceChildren(
      ...profile.seasons.map((row) => {
        const option = el("option", seasonLabel(row.season_start));
        option.value = row.season_start;
        return option;
      }),
    );
    $("ask-player").disabled = false;
    $("ask-season").disabled = state.season == null;
    renderProfile();
  } else if (directory) {
    document.title = "Players · NBA Agent";
    await loadDirectory(false);
  } else {
    document.title = "NBA Agent · The numbers behind the game";
    if (state.meta) await loadLeaders();
  }
}
function updatePlayerURL() {
  const url = new URL(location.href);
  url.searchParams.set("phase", state.phase);
  if (state.season != null) url.searchParams.set("season", state.season);
  history.replaceState(null, "", url);
}
function renderProfile() {
  const p = state.profile;
  if (!p) return;
  const row = p.seasons.find((r) => r.season_start === state.season),
    average = state.unit === "average";
  $("season").value = state.season;
  $("season").disabled = !p.seasons.length;
  $("player-meta").textContent = row
    ? `${row.teams} · ${seasonLabel(row.season_start)}`
    : "No recorded appearances for this competition.";
  $("summary").replaceChildren();
  const period = el("div", null, "nba-period");
  period.append(
    el("small", "Selected season"),
    el("strong", row ? seasonLabel(row.season_start) : "No data"),
    el(
      "span",
      `${phaseLabel(state.phase)} · ${average ? "Per game" : "Totals"}`,
    ),
  );
  $("summary").append(period);
  for (const [label, key] of [
    ["Games", "games"],
    ["Points", average ? "ppg" : "points"],
    ["Rebounds", average ? "rpg" : "rebounds"],
    ["Assists", average ? "apg" : "assists"],
  ]) {
    const item = el("div");
    item.append(el("small", label), el("strong", stat(row?.[key])));
    $("summary").append(item);
  }
  setChoices("data-phase", "phase", state.phase);
  setChoices("data-view", "view", state.view);
  setChoices("data-unit", "unit", state.unit);
  $("units").hidden = state.view === "games";
  $("games-more").hidden = true;
  $("context-copy").textContent =
    `Explore ${p.player.name}${row ? "’s " + seasonLabel(row.season_start) : ""} ${phaseLabel(state.phase).toLowerCase()}.`;
  if (state.view === "seasons") {
    const columns = [
      ["Season", "season_start"],
      ["Team", "teams"],
      ["GP", "games"],
      ["PTS", average ? "ppg" : "points"],
      ["REB", average ? "rpg" : "rebounds"],
      ["AST", average ? "apg" : "assists"],
      ["FG%", "fg_pct"],
      ["3P%", "three_pct"],
      ["FT%", "ft_pct"],
    ];
    const headers = columns.map(([label, key]) =>
      button(
        label +
          (state.sort === key ? (state.direction === -1 ? " ↓" : " ↑") : ""),
        () => {
          state.direction = state.sort === key ? -state.direction : -1;
          state.sort = key;
          renderProfile();
        },
      ),
    );
    const rows = sortRows(p.seasons, state.sort, state.direction);
    const view = table(
      headers,
      rows.map((r) =>
        columns.map(([, key]) =>
          key === "season_start"
            ? button(seasonLabel(r[key]), () => selectSeason(r.season_start))
            : key === "teams"
              ? r[key]
              : stat(r[key]),
        ),
      ),
    );
    view.querySelectorAll("tbody tr").forEach((tr, i) => {
      if (rows[i].season_start === state.season)
        tr.classList.add("nba-selected");
    });
    $("player-stats").replaceChildren(
      p.seasons.length
        ? view
        : el("p", "No recorded appearances for this competition.", "nba-empty"),
    );
  } else renderGames();
}
async function selectSeason(year) {
  state.season = Number(year);
  updatePlayerURL();
  renderProfile();
  if (state.view === "games") await loadGames(false).catch(report);
}
async function loadGames(append) {
  if (state.season == null) {
    state.games = [];
    renderGames();
    return;
  }
  const version = routeVersion,
    season = state.season,
    phase = state.phase,
    offset = append ? state.gameOffset : 0;
  if (append && offset == null) return;
  if (!append) {
    state.games = [];
    state.gameOffset = null;
    $("player-stats").replaceChildren(
      el("p", "Loading game logs…", "nba-empty"),
    );
    $("games-more").hidden = true;
  }
  $("games-more").disabled = true;
  try {
    const data = await request(
      `/v1/players/${state.profile.player.id}/games?season_start=${season}&phase=${phase}&offset=${offset}`,
    );
    if (
      version !== routeVersion ||
      season !== state.season ||
      phase !== state.phase
    )
      return;
    state.games = append ? [...state.games, ...data.games] : data.games;
    state.gameOffset = data.next_offset;
    renderGames();
  } finally {
    $("games-more").disabled = false;
  }
}
function renderGames() {
  if (state.view !== "games") return;
  $("player-stats").replaceChildren(
    state.games.length
      ? table(
          [
            "Date",
            "Team",
            "Opponent",
            "Result",
            "PTS",
            "REB",
            "AST",
            "FG",
            "3P",
          ],
          state.games.map((r) => [
            r.game_date,
            r.team,
            r.opponent,
            r.result,
            ...["points", "rebounds", "assists"].map((k) => stat(r[k])),
            r.fgm == null || r.fga == null ? "—" : `${r.fgm}–${r.fga}`,
            r.fg3m == null || r.fg3a == null ? "—" : `${r.fg3m}–${r.fg3a}`,
          ]),
        )
      : el("p", "No matching game records.", "nba-empty"),
  );
  $("games-more").hidden = state.gameOffset == null;
}
async function loadLeaders() {
  if (!$("leader-season").value) return;
  const version = ++leaderVersion;
  $("leaders").replaceChildren(el("p", "Loading season leaders…", "nba-empty"));
  const data = await request(
    `/v1/leaders?season_start=${$("leader-season").value}&category=${state.category}&minimum_games=${$("leader-min").value}`,
  );
  if (version !== leaderVersion) return;
  $("leaders").replaceChildren(
    data.leaders.length
      ? table(
          ["Player", "GP", "PTS", "REB", "AST", "FG%"],
          data.leaders.map((p) => [
            playerLink(p),
            p.games,
            ...["ppg", "rpg", "apg", "fg_pct"].map((k) => stat(p[k])),
          ]),
        )
      : el(
          "p",
          "No players meet this minimum. Try fewer games or another season.",
          "nba-empty",
        ),
  );
}
const search = debounce(async () => {
  const version = ++searchVersion,
    query = $("search").value.trim();
  $("search-results").hidden = !query;
  if (!query) return;
  try {
    const data = await request(
      `/v1/players?q=${encodeURIComponent(query)}&limit=8`,
    );
    if (version !== searchVersion || query !== $("search").value.trim()) return;
    $("search-results").replaceChildren(
      ...data.players.map((p) => {
        const result = button(
          "",
          () => navigate(`/players/${p.id}`),
          "nba-result",
        );
        result.append(
          el("span", p.name),
          el(
            "small",
            `${seasonLabel(p.first_season)} to ${seasonLabel(p.last_season)}`,
          ),
        );
        return result;
      }),
    );
    if (!data.players.length)
      $("search-results").append(el("p", "No matching players.", "nba-empty"));
  } catch (error) {
    report(error);
  }
});
async function loadDirectory(append) {
  const version = ++directoryVersion,
    query = $("directory-search").value.trim(),
    offset = append ? state.directoryOffset : 0;
  if (append && offset == null) return;
  $("directory-more").disabled = true;
  try {
    const data = await request(
      `/v1/players?q=${encodeURIComponent(query)}&limit=30&offset=${offset}`,
    );
    if (version !== directoryVersion) return;
    state.directory = append
      ? [...state.directory, ...data.players]
      : data.players;
    state.directoryOffset = data.next_offset;
    $("directory-results").replaceChildren(
      state.directory.length
        ? table(
            ["Player", "First season", "Last recorded season"],
            state.directory.map((p) => [
              playerLink(p),
              seasonLabel(p.first_season),
              seasonLabel(p.last_season),
            ]),
          )
        : el("p", "No matching players.", "nba-empty"),
    );
    $("directory-more").hidden = data.next_offset == null;
  } finally {
    $("directory-more").disabled = false;
  }
}
$("search").addEventListener("input", search);
$("search").addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("search-results").hidden = true;
  if (e.key === "Enter") {
    $("search-results").querySelector("button")?.click();
  }
});
$("directory-search").addEventListener(
  "input",
  debounce(() => loadDirectory(false).catch(report)),
);
$("directory-more").onclick = () => loadDirectory(true).catch(report);
$("leader-season").onchange = () => loadLeaders().catch(report);
$("leader-min").onchange = () => loadLeaders().catch(report);
document.querySelectorAll("[data-category]").forEach(
  (b) =>
    (b.onclick = () => {
      state.category = b.dataset.category;
      setChoices("data-category", "category", state.category);
      loadLeaders().catch(report);
    }),
);
document
  .querySelectorAll("[data-phase]")
  .forEach(
    (b) =>
      (b.onclick = () =>
        navigate(
          `/players/${state.profile.player.id}?phase=${b.dataset.phase}&season=${state.season ?? ""}`,
        )),
  );
document.querySelectorAll("[data-unit]").forEach(
  (b) =>
    (b.onclick = () => {
      state.unit = b.dataset.unit;
      renderProfile();
    }),
);
document.querySelectorAll("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      state.view = b.dataset.view;
      renderProfile();
      if (state.view === "games") loadGames(false).catch(report);
    }),
);
$("season").onchange = (e) => selectSeason(e.target.value);
$("games-more").onclick = () => loadGames(true).catch(report);
function storeSession(id) {
  state.session = id;
  try {
    if (id) localStorage.setItem("nba-session", id);
    else localStorage.removeItem("nba-session");
  } catch {
    /* Browser session remains usable without storage. */
  }
}
let visitorReady = null,
  chatLoaded = false;
async function ensureVisitor() {
  if (!visitorReady)
    visitorReady = request("/v1/visitor", { method: "POST", body: "{}" }).catch(
      (e) => {
        visitorReady = null;
        throw e;
      },
    );
  return visitorReady;
}
async function openChat(context) {
  $("chat").hidden = false;
  $("launcher").setAttribute("aria-expanded", "true");
  if (context) {
    state.context = context;
    renderContext();
  }
  $("question").focus();
  if (!chatLoaded) {
    chatLoaded = true;
    try {
      await ensureVisitor();
      await loadHistory(false);
      if (state.session) await openSession(state.session);
    } catch (e) {
      $("chat-status").textContent = e.message;
      chatLoaded = false;
    }
  }
}
function renderContext() {
  $("chat-context").hidden = !state.context;
  $("chat-context-label").textContent = state.context || "";
}
function closeChat() {
  $("chat").hidden = true;
  $("launcher").setAttribute("aria-expanded", "false");
  $("launcher").focus();
}
function userMessage(text) {
  $("messages").append(el("div", text, "chat-user"));
}
function assistantMessage(text, data = []) {
  const article = el("article");
  article.append(
    el("div", "NBA Agent", "chat-label"),
    el("div", text, "chat-text"),
  );
  for (const result of data) {
    const details = el("details");
    details.append(
      el("summary", "View data & source"),
      el("p", scope(result.query), "source-line"),
    );
    const rows = result.rows || [];
    if (rows.length) {
      const keys =
        result.query?.group_by === "game"
          ? [
              "game_date",
              "player_name",
              "opponent",
              "points",
              "rebounds",
              "assists",
            ]
          : [
              "player_name",
              ...(result.query?.group_by === "season" ? ["season_start"] : []),
              "games",
              "points",
              "rebounds",
              "assists",
              "ppg",
              "rpg",
              "apg",
            ];
      details.append(
        table(
          keys.map((k) => k.replaceAll("_", " ")),
          rows.map((r) => keys.map((k) => r[k] ?? "—")),
        ),
      );
    }
    if (result.truncated)
      details.append(
        el(
          "p",
          "Result limited. Narrow your question to see more specific records.",
          "source-line",
        ),
      );
    const url = safeSource(result.source?.source_url);
    if (url) {
      const link = el("a", "Kaggle v515");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      details.append(link);
    }
    details.append(
      el(
        "p",
        `Data through ${result.source?.max_game_date ?? "the recorded snapshot"}`,
        "source-line",
      ),
    );
    article.append(details);
  }
  $("messages").append(article);
}
async function loadHistory(append) {
  const offset = append ? state.historyOffset : 0;
  if (append && offset == null) return;
  const data = await request(`/v1/sessions?offset=${offset}`);
  state.history = append ? [...state.history, ...data.sessions] : data.sessions;
  state.historyOffset = data.next_offset;
  $("history").replaceChildren(
    ...state.history.map((s) =>
      button(s.title, () =>
        openSession(s.id).catch(
          (e) => ($("chat-status").textContent = e.message),
        ),
      ),
    ),
  );
  if (!state.history.length)
    $("history").append(el("p", "Your conversations will appear here."));
  $("history-more").hidden = data.next_offset == null;
}
let historyLoad = 0;
async function openSession(id) {
  if (state.busy) return;
  const token = ++historyLoad;
  state.busy = true;
  updateChatControls();
  $("chat-status").textContent = "Loading conversation…";
  try {
    const messages = [];
    let after = 0;
    do {
      const data = await request(
        `/v1/sessions/${encodeURIComponent(id)}?after_sequence=${after}`,
      );
      messages.push(...data.messages);
      after = data.next_after_sequence;
    } while (after != null);
    if (token !== historyLoad) return;
    storeSession(id);
    $("messages").replaceChildren();
    for (const m of messages) {
      if (m.role === "user") userMessage(m.text);
      else assistantMessage(m.text, m.data);
    }
    $("chat-status").textContent = "";
  } catch (e) {
    if (e.status === 404 || e.status === 401) {
      storeSession(null);
      $("messages").replaceChildren(el("p", "Start a new conversation."));
    }
    throw e;
  } finally {
    state.busy = false;
    updateChatControls();
  }
}
function updateChatControls() {
  for (const id of ["send", "new-chat", "question"])
    $(id).disabled = state.busy;
}
$("launcher").onclick = () => ($("chat").hidden ? openChat() : closeChat());
$("close-chat").onclick = closeChat;
$("expand-chat").onclick = () => {
  const expanded = $("chat").classList.toggle("expanded");
  $("expand-chat").setAttribute("aria-pressed", String(expanded));
  $("expand-chat").setAttribute(
    "aria-label",
    expanded ? "Collapse chat" : "Expand chat",
  );
};
$("clear-context").onclick = () => {
  state.context = null;
  renderContext();
};
$("ask-player").onclick = () => openChat(state.profile?.player.name);
$("ask-season").onclick = () =>
  openChat(
    `${state.profile.player.name} · ${seasonLabel(state.season)} · ${phaseLabel(state.phase)}`,
  );
$("new-chat").onclick = () => {
  if (state.busy) return;
  storeSession(null);
  $("messages").replaceChildren(el("p", "What would you like to explore?"));
  $("chat-status").textContent = "";
  $("question").focus();
};
$("history-more").onclick = () =>
  loadHistory(true).catch((e) => ($("chat-status").textContent = e.message));
$("chat-form").onsubmit = async (event) => {
  event.preventDefault();
  const text = $("question").value.trim();
  if (!text || state.busy) return;
  state.busy = true;
  updateChatControls();
  $("chat-status").textContent = "Checking the recorded statistics…";
  const message = state.context ? `About ${state.context}: ${text}` : text;
  try {
    await ensureVisitor();
    const reply = await request("/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: state.session }),
    });
    if (!state.session) $("messages").replaceChildren();
    storeSession(reply.session_id);
    userMessage(message);
    assistantMessage(reply.answer, reply.data);
    $("question").value = "";
    $("chat-status").textContent = "";
    $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight;
    await loadHistory(false);
  } catch (e) {
    $("chat-status").textContent = e.message;
    if (e.status === 404) storeSession(null);
    if (e.status === 401) visitorReady = null;
  } finally {
    state.busy = false;
    updateChatControls();
    $("question").focus();
  }
};
$("question").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
};
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("chat").hidden) closeChat();
});
async function start() {
  state.meta = await request("/v1/browse");
  $("leader-season").replaceChildren(
    ...state.meta.seasons.map((year) => {
      const option = el("option", seasonLabel(year));
      option.value = year;
      return option;
    }),
  );
  $("coverage").textContent =
    `Kaggle v515 · ${state.meta.first_game ?? "Unknown start"} to ${state.meta.last_game ?? "unknown cutoff"}`;
  const featured = await Promise.all(
    [201939, 201142, 201935].map((id) =>
      request(`/v1/players/${id}`).catch(() => null),
    ),
  );
  $("featured").replaceChildren(
    ...featured.filter(Boolean).map((p) => {
      const card = button(
        "",
        () => navigate(`/players/${p.player.id}`),
        "nba-playercard",
      );
      card.append(
        el("span", "EXPLORE PLAYER ↗", "nba-number"),
        el("strong", p.player.name),
        el(
          "small",
          p.seasons.length
            ? `${seasonLabel(p.seasons.at(-1).season_start)} to ${seasonLabel(p.seasons[0].season_start)}`
            : "Historical player",
        ),
      );
      return card;
    }),
  );
  await route();
}
start().catch(report);
