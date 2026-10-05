import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDelta, formatPrice, plural, STATUS } from "../src/format.ts";
import { defaultSelection, groupError, groupRule, toggleOption } from "../src/options.ts";
import type { MenuGroup } from "../src/types.ts";

const group = (min: number, max: number, unavailable: string[] = []): MenuGroup => ({
  id: "g",
  name: "Spice level",
  min_select: min,
  max_select: max,
  options: ["a", "b", "c"].map((id) => ({
    id,
    name: id.toUpperCase(),
    display_price_delta_paise: null,
    is_available: !unavailable.includes(id),
  })),
});

test("single-choice groups behave like radio buttons", () => {
  const g = group(1, 1);
  assert.deepEqual(toggleOption(g, [], "a"), ["a"]);
  assert.deepEqual(toggleOption(g, ["a"], "b"), ["b"]);
  assert.deepEqual(toggleOption(g, ["a"], "a"), ["a"]); // required: cannot unselect
  assert.deepEqual(toggleOption(group(0, 1), ["a"], "a"), []); // optional: can unselect
});

test("multi-choice groups cap at max_select and skip unavailable options", () => {
  const g = group(0, 2, ["c"]);
  let picked = toggleOption(g, [], "a");
  picked = toggleOption(g, picked, "b");
  assert.deepEqual(picked, ["a", "b"]);
  assert.deepEqual(toggleOption(g, picked, "c"), ["a", "b"]);
  assert.deepEqual(toggleOption(g, ["a"], "c"), ["a"]); // unavailable
  assert.deepEqual(toggleOption(g, picked, "a"), ["b"]);
  assert.deepEqual(toggleOption(g, [], "zzz"), []); // unknown id
});

test("rules and errors mirror the server", () => {
  assert.equal(groupRule(group(1, 1)), "Choose 1");
  assert.equal(groupRule(group(0, 1)), "Optional");
  assert.equal(groupRule(group(0, 3)), "Optional · up to 3");
  assert.equal(groupRule(group(1, 2)), "Choose 1–2");
  assert.equal(groupError(group(1, 1), []), "Please choose a spice level");
  assert.equal(groupError(group(2, 3), ["a"]), "Please choose at least 2");
  assert.equal(groupError(group(1, 1), ["a"]), null);
});

test("a required single choice with one available option is pre-selected", () => {
  assert.deepEqual(defaultSelection(group(1, 1, ["a", "b"])), ["c"]);
  assert.deepEqual(defaultSelection(group(1, 1)), []);
  assert.deepEqual(defaultSelection(group(0, 1, ["a", "b"])), []);
});

test("prices are formatted in rupees for display", () => {
  assert.equal(formatPrice(24900), "₹249");
  assert.equal(formatPrice(24950), "₹249.50");
  assert.equal(formatPrice(12345600), "₹1,23,456"); // Indian digit grouping
  assert.equal(formatPrice(null), "");
  assert.equal(formatDelta(3000), "+₹30");
  assert.equal(formatDelta(-500), "−₹5");
  assert.equal(formatDelta(0), "");
  assert.equal(plural(1, "item"), "1 item");
  assert.equal(plural(3, "item"), "3 items");
});

test("every status has text and an icon, never colour alone", () => {
  for (const info of Object.values(STATUS)) {
    assert.ok(info.label.length > 2 && info.icon.length > 0 && info.hint.length > 2);
  }
});
