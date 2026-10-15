// Menu search: filters the menu the page already has (no request to the server).

import type { MenuCategory } from "./types.ts";

/** Lowercase, without accents or punctuation, with doubled letters collapsed, so everyday
 *  spellings of the same dish find each other: "panner" and "paneer", "matar" and "mattar",
 *  "chili" and "chilli". */
export function normalize(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .replace(/(\p{L})\1+/gu, "$1")
    .trim();
}

/** True when the query has something to search for (not only spaces or punctuation). */
export function isSearching(query: string): boolean {
  return normalize(query) !== "";
}

/** The sections and dishes matching every word of the query, in menu order. A word matches
 *  part of the dish's name, its description or its section's name ("chinese" lists that
 *  section). Sections without a match are left out; an empty query returns the whole menu. */
export function filterMenu(categories: MenuCategory[], query: string): MenuCategory[] {
  const words = normalize(query).split(" ").filter(Boolean);
  if (words.length === 0) return categories;
  const found: MenuCategory[] = [];
  for (const category of categories) {
    const section = normalize(category.name);
    const items = category.items.filter((item) => {
      const text = `${normalize(item.name)} ${normalize(item.description ?? "")} ${section}`;
      return words.every((word) => text.includes(word));
    });
    if (items.length > 0) found.push({ ...category, items });
  }
  return found;
}

export function countDishes(categories: MenuCategory[]): number {
  return categories.reduce((sum, category) => sum + category.items.length, 0);
}
