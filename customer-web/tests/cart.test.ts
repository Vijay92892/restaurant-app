import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_LINES,
  addLine,
  cartFingerprint,
  emptyCart,
  lineKey,
  parseStoredCart,
  removeItems,
  setQuantity,
  toOrderRequest,
  totalQuantity,
} from "../src/cart.ts";

const line = (itemId: string, quantity = 1, modifierIds: string[] = [], notes = "") => ({
  itemId,
  name: `Dish ${itemId}`,
  quantity,
  modifierIds,
  modifierLabels: modifierIds.map((m) => `Opt: ${m}`),
  notes,
});

function add(cart = emptyCart(), ...lines: ReturnType<typeof line>[]) {
  for (const l of lines) {
    const result = addLine(cart, l);
    assert.ok(result.ok);
    cart = result.cart;
  }
  return cart;
}

test("identical dish + options + note merge into one line", () => {
  const cart = add(emptyCart(), line("a", 1, ["x", "y"]), line("a", 2, ["y", "x"]));
  assert.equal(cart.lines.length, 1);
  assert.equal(cart.lines[0]?.quantity, 3);
  assert.equal(lineKey("a", ["y", "x"], " hi "), lineKey("a", ["x", "y"], "hi"));
});

test("different options or notes stay separate lines", () => {
  const cart = add(emptyCart(), line("a", 1, ["x"]), line("a", 1, ["y"]), line("a", 1, ["x"], "no onion"));
  assert.equal(cart.lines.length, 3);
  assert.equal(totalQuantity(cart), 3);
});

test("limits: 50 per line, 100 dishes, 30 lines", () => {
  let cart = add(emptyCart(), line("a", 40));
  cart = add(cart, line("a", 30));
  assert.equal(cart.lines[0]?.quantity, 50);
  cart = add(cart, line("b", 50));
  const tooMany = addLine(cart, line("c", 1));
  assert.deepEqual(tooMany, { ok: false, reason: "too_many_dishes" });

  let wide = emptyCart();
  for (let i = 0; i < MAX_LINES; i++) wide = add(wide, line(`i${i}`));
  assert.deepEqual(addLine(wide, line("extra")), { ok: false, reason: "too_many_lines" });
});

test("setQuantity clamps and removes at zero", () => {
  let cart = add(emptyCart(), line("a", 2), line("b", 90));
  const key = cart.lines[0]!.key;
  cart = setQuantity(cart, key, 99);
  assert.equal(cart.lines[0]?.quantity, 10); // 100 total cap
  cart = setQuantity(cart, key, 0);
  assert.deepEqual(cart.lines.map((l) => l.itemId), ["b"]);
});

test("order request contains ids and quantities only — no prices or totals", () => {
  const cart = { ...add(emptyCart(), line("a", 2, ["x"], "less oil"), line("b")), notes: "  birthday " };
  const request = toOrderRequest(cart);
  assert.deepEqual(request, {
    items: [
      { menu_item_id: "a", quantity: 2, modifier_ids: ["x"], notes: "less oil" },
      { menu_item_id: "b", quantity: 1, modifier_ids: [] },
    ],
    notes: "birthday",
  });
  assert.ok(!JSON.stringify(request).match(/price|total|amount/i));
});

test("fingerprint changes only when what would be sent changes", () => {
  const cart = add(emptyCart(), line("a", 2));
  const same = add(emptyCart(), line("a", 2));
  assert.equal(cartFingerprint(cart), cartFingerprint(same));
  assert.notEqual(cartFingerprint(cart), cartFingerprint(setQuantity(cart, cart.lines[0]!.key, 3)));
});

test("removeItems drops every line of the given dishes", () => {
  const cart = add(emptyCart(), line("a", 1, ["x"]), line("a", 1, ["y"]), line("b"));
  assert.deepEqual(removeItems(cart, new Set(["a"])).lines.map((l) => l.itemId), ["b"]);
});

test("stored carts are treated as untrusted", () => {
  assert.deepEqual(parseStoredCart(null), emptyCart());
  assert.deepEqual(parseStoredCart("{not json"), emptyCart());
  const tampered = JSON.stringify({
    lines: [
      { itemId: "a", name: "A", quantity: 999, modifierIds: ["x", 5], modifierLabels: [], notes: "n".repeat(500) },
      { itemId: 7, name: "bad" },
      { itemId: "b", name: "B", quantity: 1.5, modifierIds: [], modifierLabels: [] },
    ],
    notes: 42,
  });
  const cart = parseStoredCart(tampered);
  assert.equal(cart.lines.length, 1);
  assert.equal(cart.lines[0]?.quantity, 50);
  assert.deepEqual(cart.lines[0]?.modifierIds, ["x"]);
  assert.equal(cart.lines[0]?.notes.length, 200);
  assert.equal(cart.notes, "");
});
