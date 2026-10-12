import test from "node:test";
import assert from "node:assert/strict";
import {
  money,
  scope,
  stat,
  visibleMessages,
  safeSource,
} from "../assets/format.js";

test("unknown and very small costs remain distinguishable from zero", () => {
  assert.equal(money(null), "Unavailable");
  assert.equal(money("0"), "$0.00");
  assert.equal(money("0.0000001"), "<$0.000001");
  assert.equal(money("0.00032"), "$0.00032");
  assert.equal(stat(null), "—");
  assert.equal(stat(0), "0");
});
test("scope uses start-year seasons and preserves matchup filters", () => {
  const label = scope({
    season_start: 2015,
    season_end: 2015,
    phase: "playoffs",
    playoff_round: "conference_finals",
    conference: "west",
    opposing_player: "LeBron James",
    home: false,
    last_n: 5,
  });
  assert.match(label, /2015–16/);
  assert.match(label, /Western conference finals/);
  assert.match(label, /Opposing LeBron James/);
  assert.match(label, /Away/);
  assert.match(label, /Last 5 matching games/);
});
test("internal exchanges never enter the conversation transcript", () => {
  const user = { user_visible: true, role: "user" };
  const answer = {
    user_visible: true,
    role: "analyst",
    message_type: "final_answer",
  };
  assert.deepEqual(
    visibleMessages([
      user,
      { user_visible: false, role: "analyst" },
      { user_visible: false, role: "tool" },
      answer,
    ]),
    [user, answer],
  );
});
test("source links reject executable and invalid URLs", () => {
  assert.equal(safeSource("javascript:alert(1)"), null);
  assert.equal(safeSource("data:text/html,hi"), null);
  assert.equal(safeSource("not a url"), null);
  assert.equal(
    safeSource("https://www.kaggle.com/datasets/example"),
    "https://www.kaggle.com/datasets/example",
  );
});
