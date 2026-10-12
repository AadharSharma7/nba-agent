export const number = (value, digits = 0) =>
  value == null
    ? "Unavailable"
    : Number(value).toLocaleString(undefined, {
        maximumFractionDigits: digits,
      });
export function money(value) {
  if (value == null || !Number.isFinite(Number(value))) return "Unavailable";
  const amount = Number(value);
  if (amount === 0) return "$0.00";
  if (amount < 0.000001) return "<$0.000001";
  return (
    "$" +
    amount.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: amount < 0.01 ? 6 : 4,
    })
  );
}
export function scope(query = {}) {
  const labels = [];
  const season = (y) => `${y}–${String(Number(y) + 1).slice(-2)}`;
  if (query.season_start != null)
    labels.push(
      query.season_end === query.season_start
        ? season(query.season_start)
        : `${season(query.season_start)}${query.season_end ? " to " + season(query.season_end) : " onward"}`,
    );
  else if (query.season_end != null)
    labels.push(`Through ${season(query.season_end)}`);
  labels.push(
    {
      regular: "Regular season",
      playoffs: "Playoffs",
      both: "Regular season + playoffs",
    }[query.phase] || "Regular season",
  );
  if (query.playoff_round)
    labels.push(
      [
        query.conference === "west"
          ? "Western"
          : query.conference === "east"
            ? "Eastern"
            : "",
        query.playoff_round.replaceAll("_", " "),
      ]
        .filter(Boolean)
        .join(" "),
    );
  if (query.opponent_team) labels.push(`vs. ${query.opponent_team}`);
  if (query.opposing_player) labels.push(`Opposing ${query.opposing_player}`);
  if (query.last_n) labels.push(`Last ${query.last_n} matching games`);
  if (query.home != null) labels.push(query.home ? "Home" : "Away");
  if (query.date_from || query.date_to)
    labels.push(
      `${query.date_from || "Start"} to ${query.date_to || "latest"}`,
    );
  return labels.join(" · ");
}
export const stat = (value) => (value == null ? "—" : number(value, 1));
export const visibleMessages = (messages) =>
  messages.filter(
    (m) =>
      m.user_visible &&
      (m.role === "user" || m.message_type === "final_answer"),
  );
export function safeSource(url) {
  try {
    const parsed = new URL(url);
    return ["https:", "http:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}
