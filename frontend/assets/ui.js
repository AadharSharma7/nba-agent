export const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
};
export const button = (label, onClick, className) => {
  const node = el("button", label, className);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
};
export async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed. Please try again.",
    );
    error.status = response.status;
    throw error;
  }
  return data;
}
export const seasonLabel = (year) =>
  `${year}–${String(Number(year) + 1).slice(-2)}`;
export const phaseLabel = (phase) =>
  phase === "playoffs" ? "Playoffs" : "Regular season";
export function table(headers, rows) {
  const wrapper = el("div", null, "nba-tablewrap ad-tablewrap");
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  wrapper.setAttribute(
    "aria-label",
    "Statistics table; scroll horizontally for more columns",
  );
  const tbl = el("table"),
    head = el("thead"),
    tr = el("tr"),
    body = el("tbody");
  for (const title of headers) {
    const th = el("th");
    th.scope = "col";
    th.append(title instanceof Node ? title : document.createTextNode(title));
    tr.append(th);
  }
  head.append(tr);
  tbl.append(head, body);
  for (const row of rows) {
    const line = el("tr");
    for (const value of row) {
      const td = el("td");
      td.append(
        value instanceof Node
          ? value
          : document.createTextNode(value == null ? "—" : String(value)),
      );
      line.append(td);
    }
    body.append(line);
  }
  wrapper.append(tbl);
  return wrapper;
}
export function highlight(text, query) {
  const node = el("span");
  const source = String(text ?? "");
  if (!query) {
    node.textContent = source;
    return node;
  }
  let start = 0,
    index;
  while (
    (index = source.toLowerCase().indexOf(query.toLowerCase(), start)) !== -1
  ) {
    node.append(
      document.createTextNode(source.slice(start, index)),
      el("mark", source.slice(index, index + query.length)),
    );
    start = index + query.length;
  }
  node.append(document.createTextNode(source.slice(start)));
  return node;
}
export function debounce(fn, delay = 240) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}
export function sortRows(rows, key, direction = -1) {
  return [...rows].sort((a, b) => {
    if (a[key] == null) return b[key] == null ? 0 : 1;
    if (b[key] == null) return -1;
    return (
      (typeof a[key] === "string"
        ? a[key].localeCompare(b[key])
        : a[key] - b[key]) * direction
    );
  });
}
export function localDateTimeISO(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
