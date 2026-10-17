import test from "node:test";
import assert from "node:assert/strict";
import { localDateTimeISO, sortRows, seasonLabel } from "../assets/ui.js";

test("season display uses start years", () => {
  assert.equal(seasonLabel(2015), "2015–16");
  assert.equal(seasonLabel(1999), "1999–00");
});
test("unknown statistics stay last when sorting in either direction", () => {
  const rows = [{ points: null }, { points: 20 }, { points: 0 }];
  assert.deepEqual(
    sortRows(rows, "points", -1).map((r) => r.points),
    [20, 0, null],
  );
  assert.deepEqual(
    sortRows(rows, "points", 1).map((r) => r.points),
    [0, 20, null],
  );
  assert.equal(rows[0].points, null);
});
test("date filters become explicit timezone instants, with invalid dates rejected", () => {
  assert.equal(localDateTimeISO(""), null);
  assert.equal(localDateTimeISO("invalid"), null);
  assert.equal(
    localDateTimeISO("2026-10-17T09:00:00-07:00"),
    "2026-10-17T16:00:00.000Z",
  );
});
