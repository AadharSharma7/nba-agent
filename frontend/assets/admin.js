import {
  el,
  button,
  request,
  table,
  highlight,
  debounce,
  localDateTimeISO,
} from "./ui.js?v=2";
import { number, money } from "./format.js?v=2";
const $ = (id) => document.getElementById(id);
const labels = {
  answer: "Answered",
  clarification: "Clarification",
  unsupported: "Unsupported",
  refused: "Refused",
  error: "Failed",
  pending: "Pending",
};
const state = {
  page: "overview",
  overview: null,
  sessions: [],
  next: null,
  selected: null,
  messages: [],
  calls: [],
  nextMessage: null,
  nextMetric: null,
  tab: "transcript",
  chart: "conversations",
  authenticated: false,
};
let loadVersion = 0,
  detailVersion = 0;
const dateTime = (value) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Unavailable";
$("timezone").textContent =
  `Dates and times: ${Intl.DateTimeFormat().resolvedOptions().timeZone} · Activity chart: UTC`;
function badge(status) {
  return el(
    "span",
    labels[status] ?? status,
    `ad-badge ${status === "error" ? "failed" : status === "answer" ? "answered" : status}`,
  );
}
function report(error) {
  if (error.status === 401) {
    showLogin();
    $("notice").textContent = "Your session has expired. Sign in again.";
  } else $("notice").textContent = error.message;
  $("notice").hidden = false;
}
function showLogin() {
  state.authenticated = false;
  loadVersion++;
  detailVersion++;
  state.sessions = [];
  state.messages = [];
  state.searchMessages = [];
  state.nextSearch = null;
  state.calls = [];
  state.overview = null;
  state.selected = null;
  $("workspace").hidden = true;
  $("login").hidden = false;
  $("owner").hidden = true;
  $("admin-navigation").hidden = true;
  $("location").textContent = "Sign in";
  for (const id of [
    "rows",
    "stats",
    "messages",
    "tools",
    "call-rows",
    "detailstats",
    "detail-meta",
    "detail-title",
    "model-usage",
    "outcomes",
    "chart",
  ])
    $(id).replaceChildren();
}
function showWorkspace() {
  state.authenticated = true;
  $("workspace").hidden = false;
  $("login").hidden = true;
  $("owner").hidden = false;
  $("admin-navigation").hidden = false;
  $("notice").hidden = true;
}
function dates() {
  const range = $("range").value,
    params = new URLSearchParams();
  if (range === "all") return params;
  const end = new Date();
  let start = new Date();
  if (range === "custom") {
    const from = localDateTimeISO($("from").value),
      to = localDateTimeISO($("to").value);
    if (!from || !to || from > to)
      throw new Error("Choose a valid start and end time.");
    params.set("date_from", from);
    params.set("date_to", to);
    return params;
  }
  start.setHours(0, 0, 0, 0);
  if (range !== "today")
    start.setDate(start.getDate() - (range === "month" ? 29 : 6));
  params.set("date_from", start.toISOString());
  params.set("date_to", end.toISOString());
  return params;
}
function filteredParams(offset = 0) {
  const params = dates();
  params.set("q", $("search").value.trim());
  params.set("status", $("status").value);
  params.set("offset", offset);
  params.set("limit", state.page === "overview" ? 8 : 30);
  return params;
}
function shell() {
  const detail = Boolean(state.selected);
  $("listview").hidden = detail;
  $("detail").hidden = !detail;
  $("analytics").hidden = state.page !== "overview";
  $("conversations").hidden = state.page === "costs";
  $("costs").hidden = state.page !== "costs";
  $("viewall").hidden = state.page !== "overview";
  $("conversation-title").textContent =
    state.page === "overview" ? "Recent conversations" : "Conversations";
  $("location").textContent = detail
    ? "Conversation"
    : {
        overview: "Overview",
        conversations: "Conversations",
        costs: "Usage & costs",
      }[state.page];
  $("title").textContent = {
    overview: "Your workspace, at a glance.",
    conversations: "Every conversation, in one place.",
    costs: "Understand your usage.",
  }[state.page];
  $("subtitle").textContent = {
    overview: "The conversations, questions, and costs behind NBA Agent.",
    conversations:
      "Search all visible messages and review conversation details.",
    costs: "Per-call accounting, with unknowns kept visible.",
  }[state.page];
  document
    .querySelectorAll(".ad-nav [data-page]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.page === state.page)),
    );
}
async function refresh() {
  const version = ++loadVersion;
  shell();
  $("notice").hidden = true;
  $("count").textContent = "Loading…";
  try {
    const [summary, page] = await Promise.all([
      request("/v1/admin/overview?" + dates()),
      request("/v1/admin/sessions?" + filteredParams()),
    ]);
    if (version !== loadVersion || !state.authenticated) return;
    state.overview = summary;
    state.sessions = page.sessions;
    state.next = page.next_offset;
    renderOverview();
    renderSessions(page.total);
  } catch (e) {
    if (version === loadVersion) report(e);
  }
}
function renderOverview() {
  const o = state.overview;
  if (!o) return;
  const values =
    state.page === "costs"
      ? [
          [
            "Estimated cost",
            money(o.estimated_cost_usd),
            o.cost_complete ? "Complete estimate" : "Partial · known subtotal",
          ],
          [
            "Total tokens",
            number(o.total_tokens),
            o.usage_complete ? "Input + output" : "Partial · known subtotal",
          ],
          [
            "Model calls",
            number(o.models.reduce((a, r) => a + r.calls, 0)),
            "Including failed attempts",
          ],
          ["Models", o.models.length, "Across selected conversations"],
        ]
      : [
          ["Conversations", o.conversations, "Started in selected period"],
          ["Messages", o.messages, "Visitor + assistant"],
          [
            "Estimated cost",
            money(o.estimated_cost_usd),
            o.cost_complete ? "Model API estimate" : "Partial · known subtotal",
          ],
          [
            "Median response",
            o.median_response_ms == null
              ? "Unavailable"
              : (o.median_response_ms / 1000).toFixed(1) + "s",
            "Completed application responses",
          ],
        ];
  $("stats").replaceChildren(
    ...values.map(([label, value, note]) => {
      const card = el("div", null, "ad-stat");
      card.append(
        el("span", label, "ad-stat-label"),
        el("strong", value),
        el("small", note),
      );
      return card;
    }),
  );
  $("outcomes").replaceChildren(
    ...Object.entries(labels).map(([status, label]) => {
      const row = el("div", null, "ad-outcome");
      row.append(el("span", label), el("span", o.outcomes[status] ?? 0));
      return row;
    }),
  );
  renderChart();
  $("model-usage").replaceChildren(
    o.models.length
      ? table(
          ["Model", "Calls", "Input", "Cached input¹", "Output", "Est. cost"],
          o.models.map((m) => [
            m.model,
            number(m.calls),
            number(m.input_tokens) + (m.usage_complete ? "" : " · partial"),
            number(m.cached_input_tokens),
            number(m.output_tokens),
            money(m.estimated_cost_usd) + (m.cost_complete ? "" : " · partial"),
          ]),
        )
      : el("p", "No model calls in this period.", "ad-empty"),
  );
}
function renderChart() {
  const o = state.overview;
  if (!o) return;
  const data = o.activity.slice(-14),
    cost = state.chart === "cost";
  $("chart-unit").textContent =
    `${cost ? "Known session cost" : "Sessions started"} per UTC day${o.activity.length > 14 ? " · latest 14 active days" : ""}`;
  const max = Math.max(
    1e-8,
    ...data.map(
      (d) => Number(cost ? d.estimated_cost_usd : d.conversations) || 0,
    ),
  );
  $("chart").replaceChildren(
    ...data.map((d) => {
      const day = el("div", null, "ad-day"),
        value = cost ? d.estimated_cost_usd : d.conversations,
        bar = el("div", null, "ad-bar");
      bar.style.height = `${(Number(value ?? 0) / max) * 105}px`;
      day.append(
        el(
          "span",
          cost ? money(value) + (d.cost_complete ? "" : "*") : number(value),
          "ad-value",
        ),
        bar,
        el("label", d.day.slice(5)),
      );
      return day;
    }),
  );
  if (!data.length)
    $("chart").append(el("p", "No activity in this period.", "ad-small"));
  document
    .querySelectorAll("[data-chart]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.chart === state.chart)),
    );
}
function renderSessions(total) {
  state.total = total;
  $("count").textContent =
    `${state.sessions.length} of ${total} conversations · Date filters use session start time`;
  $("rows").replaceChildren(
    state.sessions.length
      ? table(
          ["Conversation", "Started", "Outcome", "Tokens", "Est. cost"],
          state.sessions.map((s) => {
            const cell = el("div");
            const title = button(
              "",
              () => openSession(s.id).catch(report),
              "ad-subject",
            );
            title.append(highlight(s.title, $("search").value.trim()));
            cell.append(title);
            if (s.snippet) {
              const snippet = el("div", null, "ad-preview");
              snippet.append(highlight(s.snippet, $("search").value.trim()));
              cell.append(snippet);
            } else
              cell.append(
                el("div", `${s.message_count} messages`, "ad-preview"),
              );
            return [
              cell,
              dateTime(s.created_at),
              badge(s.status),
              number(s.total_tokens) + (s.usage_complete ? "" : " *"),
              money(s.estimated_cost_usd) + (s.cost_complete ? "" : " *"),
            ];
          }),
        )
      : el("p", "No conversations match these filters.", "ad-empty"),
  );
  $("more").hidden = state.next == null;
}
$("more").onclick = async () => {
  if (state.next == null) return;
  const version = loadVersion;
  $("more").disabled = true;
  try {
    const data = await request(
      "/v1/admin/sessions?" + filteredParams(state.next),
    );
    if (version !== loadVersion) return;
    state.sessions.push(...data.sessions);
    state.next = data.next_offset;
    renderSessions(data.total);
  } catch (e) {
    report(e);
  } finally {
    $("more").disabled = false;
  }
};
async function openSession(id) {
  const token = ++detailVersion;
  state.selected = id;
  state.tab = "transcript";
  $("within").value = $("search").value.trim();
  state.messages = [];
  state.searchMessages = [];
  state.nextSearch = null;
  state.calls = [];
  $("messages").replaceChildren();
  $("tools").replaceChildren();
  $("call-rows").replaceChildren();
  $("detailstats").replaceChildren();
  $("detail-meta").replaceChildren();
  $("detail-title").textContent = "Loading conversation…";
  shell();
  const [detail, metrics] = await Promise.all([
    request(`/v1/admin/sessions/${id}`),
    request(`/v1/admin/sessions/${id}/metrics`),
  ]);
  if (token !== detailVersion || !state.authenticated) return;
  state.detail = detail.session;
  state.messages = detail.messages;
  state.nextMessage = detail.next_after_sequence;
  state.calls = metrics.metrics;
  state.nextMetric = metrics.next_offset;
  renderDetail();
  if ($("within").value.trim()) await findMessages(false);
}
function renderDetail() {
  const s = state.detail;
  $("detail-title").textContent = s.title;
  $("detail-meta").replaceChildren(
    badge(s.status),
    el("span", dateTime(s.created_at)),
    el("span", `Session ${s.id}`),
  );
  $("detailstats").replaceChildren(
    ...[
      [
        "Estimated cost",
        money(s.estimated_cost_usd) + (s.cost_complete ? "" : " · partial"),
      ],
      [
        "Total tokens",
        number(s.total_tokens) + (s.usage_complete ? "" : " · partial"),
      ],
      ["Model calls", number(s.model_call_count)],
      [
        "Last response",
        s.elapsed_ms == null
          ? "Unavailable"
          : (s.elapsed_ms / 1000).toFixed(1) + "s",
      ],
    ].map(([label, value]) => {
      const node = el("div");
      node.append(el("span", label), el("strong", value));
      return node;
    }),
  );
  renderMessages();
  renderCalls();
  renderTools();
  renderDetailTab();
}
function renderDetailTab() {
  for (const tab of ["transcript", "calls", "tools"])
    $(tab).hidden = state.tab !== tab;
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.tab === state.tab)),
    );
  $("messages-more").hidden =
    state.tab === "calls" ||
    (state.tab === "transcript" && $("within").value.trim()
      ? state.nextSearch == null
      : state.nextMessage == null);
  $("messages-more").textContent =
    state.tab === "transcript" && $("within").value.trim()
      ? "Load more matches"
      : "Load remaining messages";
  $("metrics-more").hidden = state.tab !== "calls" || state.nextMetric == null;
}
function renderMessages() {
  const q = $("within").value.trim();
  const visible = (q ? (state.searchMessages ?? []) : state.messages).filter(
    (m) =>
      m.user_visible &&
      (m.role === "user" || m.message_type === "final_answer"),
  );
  let matches = 0;
  $("messages").replaceChildren(
    ...visible.map((m) => {
      const text = m.role === "user" ? m.content.message : m.content.answer,
        match = q && String(text).toLowerCase().includes(q.toLowerCase());
      if (match) matches++;
      const article = el(
          "article",
          null,
          `ad-message ${m.role === "user" ? "user" : "assistant"}`,
        ),
        meta = el("div", null, "ad-message-meta"),
        bubble = el("div", null, "ad-bubble");
      meta.append(
        el("strong", m.role === "user" ? "Visitor" : "NBA Agent"),
        el("span", dateTime(m.created_at)),
      );
      bubble.append(highlight(text, q));
      article.append(meta, bubble);
      if (m.content.data?.length) {
        const details = el("details", null, "ad-tool");
        details.append(
          el("summary", "Statistics and sources"),
          el("pre", JSON.stringify(m.content.data, null, 2)),
        );
        article.append(details);
      }
      return article;
    }),
  );
  $("matches").textContent = q
    ? `${matches} matching messages shown${state.nextSearch != null ? " · More matches available." : ""} · Searched the entire conversation. Clear search to see the transcript.`
    : "";
  if (!visible.length)
    $("messages").append(
      el("p", "No visible messages in this page.", "ad-empty"),
    );
}
function renderCalls() {
  $("call-rows").replaceChildren(
    state.calls.length
      ? table(
          [
            "Stage / status",
            "Model",
            "Input",
            "Cached¹",
            "Output",
            "Duration",
            "Est. cost",
          ],
          state.calls.map((m) => [
            `${m.stage} · ${m.status}`,
            m.model,
            number(m.input_tokens),
            number(m.cached_input_tokens),
            number(m.output_tokens),
            (m.duration_ms / 1000).toFixed(2) + "s",
            money(m.estimated_cost_usd),
          ]),
        )
      : el("p", "No recorded model calls.", "ad-empty"),
  );
}
function renderTools() {
  const logs = state.messages.filter((m) =>
    [
      "tool_request",
      "tool_result",
      "tool_error",
      "validation_feedback",
      "gate_decision",
    ].includes(m.message_type),
  );
  $("tools").replaceChildren(
    ...logs.map((m) => {
      const detail = el("details", null, "ad-tool");
      detail.append(
        el(
          "summary",
          `${m.message_type.replaceAll("_", " ")} · ${dateTime(m.created_at)}`,
        ),
        el("pre", JSON.stringify(m.content, null, 2)),
      );
      return detail;
    }),
  );
  if (!logs.length)
    $("tools").append(
      el("p", "No tool activity in the loaded messages.", "ad-empty"),
    );
}
$("messages-more").onclick = async () => {
  if (state.tab === "transcript" && $("within").value.trim()) {
    return findMessages(true).catch(report);
  }
  if (state.nextMessage == null) return;
  const token = detailVersion;
  $("messages-more").disabled = true;
  try {
    const data = await request(
      `/v1/admin/sessions/${state.selected}?after_sequence=${state.nextMessage}`,
    );
    if (token !== detailVersion) return;
    state.messages.push(...data.messages);
    state.nextMessage = data.next_after_sequence;
    renderDetail();
  } catch (e) {
    report(e);
  } finally {
    $("messages-more").disabled = false;
  }
};
$("metrics-more").onclick = async () => {
  if (state.nextMetric == null) return;
  const token = detailVersion;
  $("metrics-more").disabled = true;
  try {
    const data = await request(
      `/v1/admin/sessions/${state.selected}/metrics?offset=${state.nextMetric}`,
    );
    if (token !== detailVersion) return;
    state.calls.push(...data.metrics);
    state.nextMetric = data.next_offset;
    renderCalls();
    renderDetailTab();
  } catch (e) {
    report(e);
  } finally {
    $("metrics-more").disabled = false;
  }
};
let searchSequence = 0;
async function findMessages(append) {
  const query = $("within").value.trim(),
    token = detailVersion,
    sequence = ++searchSequence;
  if (!query) {
    renderMessages();
    renderDetailTab();
    return;
  }
  $("matches").textContent = "Searching the entire conversation…";
  $("messages-more").disabled = true;
  try {
    const offset = append ? state.nextSearch : 0;
    const page = await request(
      `/v1/admin/sessions/${state.selected}?q=${encodeURIComponent(query)}&after_sequence=${offset ?? 0}`,
    );
    if (
      token !== detailVersion ||
      sequence !== searchSequence ||
      query !== $("within").value.trim()
    )
      return;
    state.searchMessages = append
      ? [...state.searchMessages, ...page.messages]
      : page.messages;
    state.nextSearch = page.next_after_sequence;
    renderMessages();
    renderDetailTab();
  } finally {
    $("messages-more").disabled = false;
  }
}
$("within").oninput = debounce(() => findMessages(false).catch(report));
$("back").onclick = () => {
  detailVersion++;
  state.selected = null;
  shell();
};
document.querySelectorAll("[data-page]").forEach(
  (b) =>
    (b.onclick = () => {
      detailVersion++;
      state.selected = null;
      state.page = b.dataset.page;
      refresh();
    }),
);
document.querySelectorAll("[data-tab]").forEach(
  (b) =>
    (b.onclick = () => {
      state.tab = b.dataset.tab;
      renderDetailTab();
    }),
);
document.querySelectorAll("[data-chart]").forEach(
  (b) =>
    (b.onclick = () => {
      state.chart = b.dataset.chart;
      renderChart();
    }),
);
$("range").onchange = () => {
  $("custom").hidden = $("range").value !== "custom";
  if ($("range").value !== "custom") refresh();
};
$("apply-dates").onclick = () => refresh();
$("search").oninput = debounce(() => refresh());
$("status").onchange = () => refresh();
$("login-form").onsubmit = async (event) => {
  event.preventDefault();
  $("sign-in").disabled = true;
  $("notice").hidden = true;
  try {
    await request("/v1/owner/login", {
      method: "POST",
      body: JSON.stringify({
        password: $("password").value,
        code: $("code").value,
      }),
    });
    $("password").value = "";
    $("code").value = "";
    showWorkspace();
    await refresh();
  } catch (error) {
    $("code").value = "";
    report(error);
  } finally {
    $("sign-in").disabled = false;
  }
};
$("logout").onclick = async () => {
  try {
    await request("/v1/owner/logout", { method: "POST", body: "{}" });
    showLogin();
  } catch (error) {
    report(error);
  }
};
async function start() {
  const auth = await request("/v1/owner");
  $("setup-note").hidden = auth.configured;
  $("login-form").hidden = !auth.configured;
  if (auth.authenticated) {
    showWorkspace();
    await refresh();
  } else showLogin();
}
start().catch(report);
