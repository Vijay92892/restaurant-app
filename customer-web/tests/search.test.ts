import assert from "node:assert/strict";
import { test } from "node:test";

import { countDishes, filterMenu, isSearching, normalize } from "../src/search.ts";
import type { MenuCategory, MenuItem } from "../src/types.ts";

const dish = (name: string, description: string | null = null): MenuItem => ({
  id: name,
  name,
  description,
  dietary: null,
  display_price_paise: null,
  image_url: null,
  thumbnail_url: null,
  is_available: true,
  orderable: true,
  modifier_group_ids: [],
});

const MENU: MenuCategory[] = [
  { id: "c1", name: "Chinese", description: null, items: [dish("Veg Manchurian Dry / Gravy"), dish("Chilli Paneer Dry / Gravy"), dish("Hakka Noodle")] },
  { id: "c2", name: "Paneer Special", description: null, items: [dish("Kadhai Paneer"), dish("Mattar Paneer"), dish("Shahi Paneer", "Rich cashew gravy")] },
  { id: "c3", name: "Indian Bread", description: null, items: [dish("Butter Naan"), dish("Garlic Naan"), dish("Plain Naan")] },
];

const names = (categories: MenuCategory[]) => categories.flatMap((c) => c.items.map((i) => i.name));

test("normalizing ignores case, accents, punctuation and doubled letters", () => {
  assert.equal(normalize("  Chilli Paneer Dry / Gravy "), "chili paner dry gravy");
  assert.equal(normalize("Crème Brûlée"), "creme brule");
  assert.equal(normalize("Gol Gappe (6 pcs)"), "gol gape 6 pcs");
  assert.equal(normalize("Mattar"), normalize("Matar"));
  assert.equal(normalize("Panner"), normalize("Paneer"));
});

test("an empty or punctuation-only query is not a search and keeps the whole menu", () => {
  for (const query of ["", "   ", " / ( ) "]) {
    assert.equal(isSearching(query), false);
    assert.equal(filterMenu(MENU, query), MENU);
  }
  assert.equal(isSearching("naan"), true);
});

test("dishes are found by any part of their name, in menu order", () => {
  assert.deepEqual(names(filterMenu(MENU, "naan")), ["Butter Naan", "Garlic Naan", "Plain Naan"]);
  assert.deepEqual(names(filterMenu(MENU, "NOOD")), ["Hakka Noodle"]);
});

test("everyday spellings find the dish", () => {
  assert.deepEqual(names(filterMenu(MENU, "panner")), [
    "Chilli Paneer Dry / Gravy", "Kadhai Paneer", "Mattar Paneer", "Shahi Paneer",
  ]);
  assert.deepEqual(names(filterMenu(MENU, "matar paneer")), ["Mattar Paneer"]);
  assert.deepEqual(names(filterMenu(MENU, "chili")), ["Chilli Paneer Dry / Gravy"]);
});

test("every word must match", () => {
  assert.deepEqual(names(filterMenu(MENU, "garlic naan")), ["Garlic Naan"]);
  assert.deepEqual(filterMenu(MENU, "garlic paneer"), []);
});

test("a section's name lists its dishes, and descriptions count too", () => {
  assert.deepEqual(names(filterMenu(MENU, "chinese")), [
    "Veg Manchurian Dry / Gravy", "Chilli Paneer Dry / Gravy", "Hakka Noodle",
  ]);
  assert.deepEqual(names(filterMenu(MENU, "cashew")), ["Shahi Paneer"]);
});

test("sections without a match are left out and the menu itself is never changed", () => {
  const before = JSON.stringify(MENU);
  const found = filterMenu(MENU, "gravy");
  assert.deepEqual(found.map((c) => c.name), ["Chinese", "Paneer Special"]);
  assert.equal(countDishes(found), 3);
  assert.equal(countDishes(MENU), 9);
  assert.deepEqual(filterMenu(MENU, "pizza"), []);
  assert.equal(JSON.stringify(MENU), before);
});
