// Option-group selection rules (pure; mirrors the server's validation for instant feedback).

import type { MenuGroup } from "./types.ts";

/** Toggle an option: single-choice groups behave like radio buttons, others like checkboxes
 *  capped at max_select. Unavailable options can never be selected. */
export function toggleOption(group: MenuGroup, selected: string[], optionId: string): string[] {
  const option = group.options.find((o) => o.id === optionId);
  if (!option || !option.is_available) return selected;
  if (group.max_select === 1) {
    return selected[0] === optionId && group.min_select === 0 ? [] : [optionId];
  }
  if (selected.includes(optionId)) return selected.filter((id) => id !== optionId);
  if (selected.length >= group.max_select) return selected;
  return [...selected, optionId];
}

/** Human-readable rule, e.g. "Choose 1", "Choose up to 2", "Optional". */
export function groupRule(group: MenuGroup): string {
  const { min_select: min, max_select: max } = group;
  if (min === 0) return max === 1 ? "Optional" : `Optional · up to ${max}`;
  if (min === max) return `Choose ${min}`;
  return `Choose ${min}–${max}`;
}

/** null when valid, otherwise a short message for this group. */
export function groupError(group: MenuGroup, selected: string[]): string | null {
  if (selected.length < group.min_select) {
    return group.min_select === 1 ? `Please choose a ${group.name.toLowerCase()}` : `Please choose at least ${group.min_select}`;
  }
  if (selected.length > group.max_select) return `Choose at most ${group.max_select}`;
  return null;
}

/** Default picks: nothing pre-selected, except a required single choice with exactly one
 *  available option (no point making the diner tap it). */
export function defaultSelection(group: MenuGroup): string[] {
  const available = group.options.filter((o) => o.is_available);
  if (group.min_select === 1 && group.max_select === 1 && available.length === 1 && available[0]) {
    return [available[0].id];
  }
  return [];
}
