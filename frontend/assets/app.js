import {
  number,
  money,
  scope,
  stat,
  visibleMessages,
  safeSource,
} from "./format.js";

const $ = (id) => document.getElementById(id);
const state = {
  session: null,
  id: null,
  history: [],
  next: null,
  busy: false,
  loading: false,
  health: null,
  load: 0,
  list: 0,
  metricsLoad: 0,
  lastQuestion: null,
};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function button(text, action, className = "text-button") {
  const node = el("button", text, className);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* Fall back to a safe status message. */
    }
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Request failed (${response.status}). Please try again.`,
    );
  }
  return response.json();
}
function notice(text = "") {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}
function updateSend() {
  $("send").disabled =
    state.busy ||
    state.loading ||
    !$("question").value.trim() ||
    !state.health?.llm_configured ||
    state.health?.status !== "ok";
  $("question").disabled = state.busy || state.loading;
  $("new-chat").disabled = state.busy;
  $("character-count").textContent =
    $("question").value.length > 1600
      ? `${$("question").value.length}/2,000`
      : "";
  document.querySelectorAll(".history-entry,[data-prompt]").forEach((node) => {
    node.disabled = state.busy;
  });
}
function updateMetrics() {
  const session = state.session;
  const hasSession = Boolean(state.id);
  $("tokens").textContent = number(hasSession ? session?.total_tokens : 0);
  $("cost").textContent = money(hasSession ? session?.estimated_cost_usd : 0);
  $("calls").textContent = number(hasSession ? session?.model_call_count : 0);
  $("partial-label").hidden =
    !hasSession ||
    !session ||
    (session.cost_complete !== false && session.usage_complete !== false);
}
async function checkHealth() {
  try {
    state.health = await api("/health");
    const ready = state.health.status === "ok" && state.health.llm_configured;
    $("connection-label").textContent = ready
      ? "Connected locally"
      : state.health.status !== "ok"
        ? "Dataset not ready"
        : "API key needed";
    $("connection-dot").className = `status-dot ${ready ? "ready" : "offline"}`;
    if (!ready)
      notice(
        state.health.status !== "ok"
          ? "The NBA dataset needs to be imported before you can chat."
          : "Add your OpenAI API key to backend/.env and restart the backend to enable chat. You can still browse past conversations.",
      );
    else notice();
  } catch {
    state.health = null;
    $("connection-label").textContent = "Backend unavailable";
    $("connection-dot").className = "status-dot offline";
    notice(
      "Cannot connect to the local backend. Start it, then use the connection refresh button.",
    );
  }
  updateSend();
}
function renderHistory() {
  const container = $("history");
  container.replaceChildren();
  if (!state.history.length)
    container.append(
      el(
        "p",
        $("search").value
          ? "No matching conversations."
          : "Your conversations will appear here.",
        "history-empty",
      ),
    );
  for (const session of state.history) {
    const entry = button(
      session.title,
      () => openSession(session.id),
      "history-entry" + (session.id === state.id ? " active" : ""),
    );
    entry.title = session.title;
    entry.dataset.sessionId = session.id;
    if (session.id === state.id) entry.setAttribute("aria-current", "page");
    container.append(entry);
  }
  $("more-history").hidden = state.next == null;
  updateSend();
}
async function loadHistory(more = false) {
  const sequence = ++state.list;
  $("more-history").disabled = true;
  try {
    const result = await api(
      `/v1/sessions?limit=30&offset=${more ? (state.next ?? 0) : 0}&q=${encodeURIComponent($("search").value.trim())}`,
    );
    if (sequence !== state.list) return;
    state.history = more
      ? [
          ...state.history,
          ...result.sessions.filter(
            (s) => !state.history.some((existing) => existing.id === s.id),
          ),
        ]
      : result.sessions;
    state.next = result.next_offset;
    renderHistory();
  } catch {
    if (sequence === state.list) {
      $("history").replaceChildren(
        el(
          "p",
          "Could not load conversations. Use refresh to try again.",
          "load-error",
        ),
      );
    }
  } finally {
    if (sequence === state.list) $("more-history").disabled = false;
  }
}
function setSessionUrl(id) {
  const url = new URL(location.href);
  if (id) url.searchParams.set("session", id);
  else url.searchParams.delete("session");
  history.replaceState(null, "", url);
}
function syncSidebar() {
  const mobile = innerWidth <= 760;
  const open = mobile
    ? document.body.classList.contains("sidebar-open")
    : !document.body.classList.contains("sidebar-collapsed");
  $("sidebar").inert = !open;
  document.querySelector(".main").inert = mobile && open;
  $("sidebar-scrim").hidden = !(mobile && open);
  $("toggle-sidebar").setAttribute("aria-expanded", String(open));
}
function closeSidebar() {
  const wasOpen = document.body.classList.contains("sidebar-open");
  document.body.classList.remove("sidebar-open");
  syncSidebar();
  if (wasOpen) $("toggle-sidebar").focus();
}
function newChat() {
  if (state.busy) return;
  ++state.load;
  ++state.metricsLoad;
  state.id = null;
  state.session = null;
  state.loading = false;
  state.lastQuestion = null;
  setSessionUrl(null);
  $("session-title").textContent = "New conversation";
  $("messages").replaceChildren();
  $("welcome").hidden = false;
  $("question").value = "";
  updateMetrics();
  renderHistory();
  closeSidebar();
  updateSend();
  $("question").focus();
}
async function sessionData(id) {
  let sequence = 0,
    messages = [],
    session;
  do {
    const page = await api(
      `/v1/sessions/${encodeURIComponent(id)}?limit=500&after_sequence=${sequence}`,
    );
    session = page.session;
    messages.push(...page.messages);
    sequence = page.next_after_sequence;
  } while (sequence != null);
  return { session, messages: visibleMessages(messages) };
}
async function openSession(id) {
  if (state.busy) return;
  const token = ++state.load;
  state.loading = true;
  state.id = id;
  state.session = null;
  updateMetrics();
  updateSend();
  closeSidebar();
  renderHistory();
  $("welcome").hidden = true;
  $("messages").replaceChildren(
    el("p", "Loading conversation…", "loading-history"),
  );
  try {
    const data = await sessionData(id);
    if (token !== state.load) return;
    state.session = data.session;
    const first = data.messages.find((m) => m.role === "user");
    $("session-title").textContent = first?.content?.message || "Conversation";
    $("messages").replaceChildren();
    for (const message of data.messages) {
      if (message.role === "user") {
        renderUser(message.content.message);
        state.lastQuestion = message.content.message;
      } else renderAnswer(message.content, state.lastQuestion);
    }
    setSessionUrl(id);
    updateMetrics();
    scrollBottom();
  } catch (error) {
    if (token !== state.load) return;
    const box = el("div", null, "load-error");
    box.append(
      el("p", error.message),
      button("Try loading again", () => openSession(id)),
    );
    $("messages").replaceChildren(box);
    $("session-title").textContent = "Conversation unavailable";
  } finally {
    if (token === state.load) {
      state.loading = false;
      updateSend();
    }
  }
}
function renderUser(text) {
  const article = el("article", null, "message user");
  article.append(el("div", text, "user-bubble"));
  $("messages").append(article);
}
function prose(text) {
  const node = el("div", null, "answer-text");
  // Render only bold spans; everything else remains text, including untrusted HTML.
  String(text || "")
    .split(/(\*\*[^*]+\*\*)/g)
    .forEach((part) =>
      node.append(
        part.startsWith("**") && part.endsWith("**")
          ? el("strong", part.slice(2, -2))
          : document.createTextNode(part),
      ),
    );
  return node;
}
function table(headers, rows) {
  const wrapper = el("div", null, "table-scroll");
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  wrapper.setAttribute(
    "aria-label",
    "Statistics table; scroll horizontally for more columns",
  );
  const table = el("table"),
    head = el("thead"),
    row = el("tr");
  headers.forEach((label) => {
    const th = el("th", label);
    th.scope = "col";
    row.append(th);
  });
  head.append(row);
  table.append(head);
  const body = el("tbody");
  rows.forEach((values) => {
    const tr = el("tr");
    values.forEach((value) => tr.append(el("td", value)));
    body.append(tr);
  });
  table.append(body);
  wrapper.append(table);
  return wrapper;
}
function renderEvidence(data) {
  const container = el("section", null, "evidence");
  container.append(el("div", scope(data.query), "scope"));
  const rows = data.rows || [],
    group = data.query?.group_by || "player";
  if (rows.length === 1 && group === "player") {
    const row = rows[0],
      cards = el("div", null, "stat-cards");
    for (const [label, value] of [
      ["GAMES", row.games],
      ["POINTS / GAME", row.ppg],
      ["REBOUNDS / GAME", row.rpg],
      ["ASSISTS / GAME", row.apg],
    ]) {
      const card = el("div", null, "stat-card");
      card.append(el("strong", stat(value)), el("span", label));
      cards.append(card);
    }
    container.append(cards);
  } else if (rows.length > 1 && group === "player") {
    container.append(
      table(
        ["Statistic", ...rows.map((row) => row.player_name)],
        [
          ["Games", "games"],
          ["Points / game", "ppg"],
          ["Rebounds / game", "rpg"],
          ["Assists / game", "apg"],
          ["FG %", "fg_pct"],
        ].map(([label, key]) => [label, ...rows.map((row) => stat(row[key]))]),
      ),
    );
  }
  if (data.players_without_returned_rows?.length)
    container.append(
      el(
        "p",
        `No returned rows for ${data.players_without_returned_rows.map((p) => p.name).join(", ")}.`,
        "source-line",
      ),
    );
  const details = el("details", null, "data-details");
  details.append(
    el(
      "summary",
      `View data & source${rows.length ? ` · ${rows.length} ${rows.length === 1 ? "row" : "rows"}` : ""}`,
    ),
  );
  if (data.truncated)
    details.append(
      el(
        "p",
        "This result is limited. Narrow your question to see a more specific set of records.",
        "source-line",
      ),
    );
  if (rows.length) {
    if (group === "game")
      details.append(
        table(
          [
            "Date",
            "Player",
            "Opponent",
            "MIN",
            "PTS",
            "REB",
            "AST",
            "STL",
            "BLK",
            "TOV",
          ],
          rows.map((r) => [
            r.game_date,
            r.player_name,
            r.opponent,
            ...[
              "minutes",
              "points",
              "rebounds",
              "assists",
              "steals",
              "blocks",
              "turnovers",
            ].map((k) => stat(r[k])),
          ]),
        ),
      );
    else
      details.append(
        table(
          [
            "Player",
            ...(group === "season" ? ["Season"] : []),
            "GP",
            "PTS",
            "REB",
            "AST",
            "PPG",
            "RPG",
            "APG",
            "FG %",
            "3P %",
            "FT %",
          ],
          rows.map((r) => [
            r.player_name,
            ...(group === "season"
              ? [`${r.season_start}–${String(r.season_start + 1).slice(-2)}`]
              : []),
            ...[
              "games",
              "points",
              "rebounds",
              "assists",
              "ppg",
              "rpg",
              "apg",
              "fg_pct",
              "three_pct",
              "ft_pct",
            ].map((k) => stat(r[k])),
          ]),
        ),
      );
  } else details.append(el("p", "No matching statistics were returned."));
  const source = el("p", null, "source-line");
  const url = safeSource(data.source?.source_url);
  if (url) {
    const link = el(
      "a",
      `Kaggle · version ${data.source.version ?? "unknown"}`,
    );
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    source.append(link);
  } else
    source.append(document.createTextNode("Kaggle historical NBA dataset"));
  if (data.source?.max_game_date)
    source.append(
      document.createTextNode(` · Data through ${data.source.max_game_date}`),
    );
  source.append(
    el("br"),
    document.createTextNode(
      "— means unavailable. Shooting percentages use total makes ÷ total attempts.",
    ),
  );
  details.append(source);
  container.append(details);
  return container;
}
function renderAnswer(response, question) {
  const article = el("article", null, "message assistant"),
    label = el("div", null, "assistant-label"),
    mark = el("img");
  mark.src = "/assets/mark.svg";
  mark.alt = "";
  label.append(mark, document.createTextNode("NBA Agent"));
  const statusLabels = {
    clarification: "A quick clarification",
    unsupported: "Outside current coverage",
    refused: "NBA questions only",
    error: "Could not complete",
  };
  if (statusLabels[response.status])
    label.append(el("span", statusLabels[response.status], "status-label"));
  article.append(label, prose(response.answer));
  (response.data || []).forEach((data) => article.append(renderEvidence(data)));
  if (response.status === "error" && question)
    article.append(
      button("Ask again", () => {
        if (!state.busy) {
          $("question").value = question;
          updateSend();
          $("question").focus();
        }
      }),
    );
  $("messages").append(article);
  return article;
}
function scrollBottom() {
  requestAnimationFrame(() => {
    $("conversation-scroll").scrollTop = $("conversation-scroll").scrollHeight;
  });
}
async function send(event) {
  event.preventDefault();
  const message = $("question").value.trim();
  if (
    !message ||
    state.busy ||
    state.loading ||
    !state.health?.llm_configured ||
    state.health.status !== "ok"
  )
    return;
  state.busy = true;
  state.lastQuestion = message;
  updateSend();
  $("welcome").hidden = true;
  renderUser(message);
  $("question").value = "";
  const waiting = el("div", null, "message pending");
  waiting.append(
    el("span", null, "spinner"),
    el("span", "Working on your question…"),
  );
  $("messages").append(waiting);
  scrollBottom();
  try {
    const response = await api("/v1/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        ...(state.id ? { session_id: state.id } : {}),
      }),
    });
    waiting.remove();
    state.id = response.session_id;
    setSessionUrl(state.id);
    if (!state.session) $("session-title").textContent = message;
    renderAnswer(response, message);
    scrollBottom();
    try {
      const page = await api(
        `/v1/sessions/${encodeURIComponent(state.id)}?limit=1`,
      );
      state.session = page.session;
      updateMetrics();
    } catch {
      notice(
        "Your answer is ready, but the session metrics could not refresh. Reopen the conversation to refresh them.",
      );
    }
    if ($("metrics-dialog").open) await loadMetrics();
    await loadHistory();
  } catch (error) {
    waiting.remove();
    renderAnswer(
      {
        status: "error",
        answer: `${error.message}\nIf the connection was interrupted, the request may still have completed. Check conversation history before sending it again.`,
      },
      message,
    );
    await loadHistory();
    scrollBottom();
  } finally {
    state.busy = false;
    updateSend();
    $("question").focus();
  }
}
async function loadMetrics() {
  const token = ++state.metricsLoad,
    sessionId = state.id;
  const container = $("metrics-content");
  container.replaceChildren();
  if (!sessionId) {
    container.append(
      el(
        "p",
        "Session metrics will appear after your first question.",
        "dialog-note",
      ),
    );
    return;
  }
  container.append(el("p", "Loading session details…", "dialog-note"));
  try {
    const summary = await api(
      `/v1/sessions/${encodeURIComponent(sessionId)}?limit=1`,
    );
    if (token !== state.metricsLoad) return;
    state.session = summary.session;
    updateMetrics();
    let offset = 0,
      records = [];
    do {
      const page = await api(
        `/v1/sessions/${encodeURIComponent(sessionId)}/metrics?limit=500&offset=${offset}`,
      );
      records.push(...page.metrics);
      offset = page.next_offset;
      if (token !== state.metricsLoad) return;
    } while (offset != null);
    container.replaceChildren();
    const grid = el("dl", null, "metric-grid");
    const s = summary.session;
    for (const [label, value] of [
      ["Input tokens", number(s.input_tokens)],
      ["Output tokens", number(s.output_tokens)],
      ["Cached input", number(s.cached_input_tokens)],
      ["Total tokens", number(s.total_tokens)],
      ["Estimated cost", money(s.estimated_cost_usd)],
      ["Model calls", number(s.model_call_count)],
    ]) {
      const item = el("div", null, "metric-tile");
      item.append(el("dt", label), el("dd", value));
      grid.append(item);
    }
    container.append(grid);
    if (s.usage_complete !== true || s.cost_complete !== true)
      container.append(
        el(
          "p",
          s.metrics_started_at
            ? "Partial estimate: some usage or pricing is unavailable. Totals include only recorded values."
            : "Metrics were not recorded for this older session.",
          "dialog-note",
        ),
      );
    container.append(
      el(
        "p",
        "Cached tokens are included in input tokens. Costs are estimates in USD. Calls appear after each chat request finishes.",
        "dialog-note",
      ),
    );
    const byStage = new Map();
    for (const record of records) {
      const stage = byStage.get(record.stage) || {
        calls: 0,
        cost: 0,
        known: 0,
        complete: true,
      };
      stage.calls++;
      if (record.estimated_cost_usd != null) {
        stage.cost += Number(record.estimated_cost_usd);
        stage.known++;
      } else stage.complete = false;
      byStage.set(record.stage, stage);
    }
    if (records.length) {
      container.append(
        el("h3", "By pipeline stage", "section-caption"),
        table(
          ["Stage", "Calls", "Estimated cost"],
          [...byStage].map(([name, s]) => [
            name === "gatekeeper" ? "Gatekeeper" : "Analyst",
            number(s.calls),
            money(s.known ? s.cost : null) +
              (!s.complete && s.known ? " (partial)" : ""),
          ]),
        ),
      );
      const questions = [...new Set(records.map((r) => r.user_message_id))];
      container.append(
        el("h3", "Individual model calls", "section-caption"),
        table(
          [
            "Question / call",
            "Stage",
            "Model",
            "Tokens",
            "Cost",
            "Duration",
            "Status",
          ],
          records.map((r) => [
            `${questions.indexOf(r.user_message_id) + 1} / ${r.call_sequence}`,
            r.stage === "gatekeeper" ? "Gatekeeper" : "Analyst",
            r.model,
            number(r.total_tokens),
            money(r.estimated_cost_usd),
            `${number(r.duration_ms / 1000, 2)} s`,
            r.status,
          ]),
        ),
      );
    } else
      container.append(
        el("p", "No recorded model calls in this conversation.", "dialog-note"),
      );
  } catch {
    if (token === state.metricsLoad) {
      container.replaceChildren(
        el("p", "Could not load metrics.", "dialog-note"),
        button("Try again", loadMetrics),
      );
    }
  }
}
$("chat-form").addEventListener("submit", send);
$("question").addEventListener("input", updateSend);
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$("send").disabled) $("chat-form").requestSubmit();
  }
});
$("new-chat").addEventListener("click", newChat);
$("refresh-history").addEventListener("click", () => loadHistory());
$("check-connection").addEventListener("click", checkHealth);
$("more-history").addEventListener("click", () => loadHistory(true));
let searchTimer;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadHistory(), 250);
});
$("toggle-sidebar").addEventListener("click", () => {
  if (innerWidth <= 760) {
    document.body.classList.toggle("sidebar-open");
    syncSidebar();
    $("close-sidebar").focus();
  } else {
    document.body.classList.toggle("sidebar-collapsed");
    syncSidebar();
  }
});
window.addEventListener("resize", syncSidebar);
$("close-sidebar").addEventListener("click", closeSidebar);
$("sidebar-scrim").addEventListener("click", closeSidebar);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeSidebar();
  if (
    event.key === "Tab" &&
    innerWidth <= 760 &&
    document.body.classList.contains("sidebar-open")
  ) {
    const focusable = [
      ...$("sidebar").querySelectorAll("button:not([disabled]),input"),
    ].filter((n) => n.offsetParent !== null);
    const first = focusable[0],
      last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
$("open-metrics").addEventListener("click", () => {
  $("metrics-dialog").showModal();
  loadMetrics();
});
$("close-metrics").addEventListener("click", () => $("metrics-dialog").close());
$("metrics-dialog").addEventListener("close", () => {
  ++state.metricsLoad;
});
$("metrics-dialog").addEventListener("click", (event) => {
  if (event.target === $("metrics-dialog")) {
    const bounds = $("metrics-dialog").getBoundingClientRect();
    if (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    )
      $("metrics-dialog").close();
  }
});
document.querySelectorAll("[data-prompt]").forEach((node) =>
  node.addEventListener("click", () => {
    $("question").value = node.dataset.prompt;
    updateSend();
    $("question").focus();
  }),
);
syncSidebar();
const initial = new URL(location.href).searchParams.get("session");
const validInitial = initial && /^[0-9a-f-]{36}$/i.test(initial);
if (initial && !validInitial) setSessionUrl(null);
// Start history loading before enabling input for a bookmarked conversation.
// Capture the initial URL before user actions can replace it.
await Promise.all([
  validInitial ? openSession(initial) : Promise.resolve(),
  checkHealth(),
  loadHistory(),
]);
