// Cart model: pure functions, no DOM (unit-tested with node --test).
// The server re-validates everything; these limits only give fast, friendly feedback.

import type { OrderRequest } from "./types.ts";

export const MAX_LINES = 30;
export const MAX_LINE_QUANTITY = 50;
export const MAX_TOTAL_QUANTITY = 100;
export const MAX_LINE_NOTES = 200;
export const MAX_ORDER_NOTES = 500;

export interface CartLine {
  key: string;
  itemId: string;
  name: string;
  quantity: number;
  modifierIds: string[];
  modifierLabels: string[];
  notes: string;
}

export interface Cart {
  lines: CartLine[];
  notes: string;
}

export const emptyCart = (): Cart => ({ lines: [], notes: "" });

/** Identical dish + options + note ⇒ same line (quantities merge). */
export function lineKey(itemId: string, modifierIds: string[], notes: string): string {
  return [itemId, [...modifierIds].sort().join(","), notes.trim()].join("|");
}

export function totalQuantity(cart: Cart): number {
  return cart.lines.reduce((sum, line) => sum + line.quantity, 0);
}

export type AddResult = { ok: true; cart: Cart } | { ok: false; reason: "too_many_lines" | "too_many_dishes" };

export function addLine(cart: Cart, line: Omit<CartLine, "key">): AddResult {
  const key = lineKey(line.itemId, line.modifierIds, line.notes);
  if (totalQuantity(cart) + line.quantity > MAX_TOTAL_QUANTITY) {
    return { ok: false, reason: "too_many_dishes" };
  }
  const existing = cart.lines.find((l) => l.key === key);
  if (existing) {
    const quantity = Math.min(existing.quantity + line.quantity, MAX_LINE_QUANTITY);
    return {
      ok: true,
      cart: { ...cart, lines: cart.lines.map((l) => (l.key === key ? { ...l, quantity } : l)) },
    };
  }
  if (cart.lines.length >= MAX_LINES) return { ok: false, reason: "too_many_lines" };
  return { ok: true, cart: { ...cart, lines: [...cart.lines, { ...line, notes: line.notes.trim(), key }] } };
}

export function setQuantity(cart: Cart, key: string, quantity: number): Cart {
  if (quantity <= 0) return { ...cart, lines: cart.lines.filter((l) => l.key !== key) };
  const others = totalQuantity(cart) - (cart.lines.find((l) => l.key === key)?.quantity ?? 0);
  const allowed = Math.min(quantity, MAX_LINE_QUANTITY, MAX_TOTAL_QUANTITY - others);
  return { ...cart, lines: cart.lines.map((l) => (l.key === key ? { ...l, quantity: allowed } : l)) };
}

export function removeItems(cart: Cart, itemIds: Set<string>): Cart {
  return { ...cart, lines: cart.lines.filter((l) => !itemIds.has(l.itemId)) };
}

export function toOrderRequest(cart: Cart): OrderRequest {
  const request: OrderRequest = {
    items: cart.lines.map((line) => ({
      menu_item_id: line.itemId,
      quantity: line.quantity,
      modifier_ids: line.modifierIds,
      ...(line.notes ? { notes: line.notes } : {}),
    })),
  };
  const notes = cart.notes.trim();
  if (notes) request.notes = notes;
  return request;
}

/** Stable fingerprint of what would be sent: a retry of the same cart reuses its key. */
export function cartFingerprint(cart: Cart): string {
  return JSON.stringify(toOrderRequest(cart));
}

/** Restores a cart from storage, discarding anything malformed (storage is untrusted too). */
export function parseStoredCart(raw: string | null): Cart {
  if (!raw) return emptyCart();
  try {
    const data = JSON.parse(raw) as Partial<Cart>;
    const lines = Array.isArray(data.lines) ? data.lines : [];
    let cart = emptyCart();
    for (const line of lines.slice(0, MAX_LINES)) {
      if (
        typeof line?.itemId !== "string" ||
        typeof line.name !== "string" ||
        !Number.isInteger(line.quantity) ||
        !Array.isArray(line.modifierIds) ||
        !Array.isArray(line.modifierLabels)
      ) {
        continue;
      }
      const result = addLine(cart, {
        itemId: line.itemId,
        name: line.name.slice(0, 80),
        quantity: Math.max(1, Math.min(line.quantity, MAX_LINE_QUANTITY)),
        modifierIds: line.modifierIds.filter((m): m is string => typeof m === "string").slice(0, 20),
        modifierLabels: line.modifierLabels.filter((m): m is string => typeof m === "string").slice(0, 20),
        notes: typeof line.notes === "string" ? line.notes.slice(0, MAX_LINE_NOTES) : "",
      });
      if (result.ok) cart = result.cart;
    }
    cart.notes = typeof data.notes === "string" ? data.notes.slice(0, MAX_ORDER_NOTES) : "";
    return cart;
  } catch {
    return emptyCart();
  }
}
