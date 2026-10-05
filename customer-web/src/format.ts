// Display formatting (pure). Prices are shown as menu information only.

import type { Dietary, OrderStatus } from "./types.ts";

const wholeRupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const rupeesAndPaise = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** ₹249 for whole rupees, ₹249.50 otherwise (never ₹249.5). */
export function formatPrice(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "";
  return (paise % 100 === 0 ? wholeRupees : rupeesAndPaise).format(paise / 100);
}

export function formatDelta(paise: number | null | undefined): string {
  if (!paise) return "";
  return `${paise > 0 ? "+" : "−"}${formatPrice(Math.abs(paise))}`;
}

export const DIETARY_LABEL: Record<Dietary, string> = {
  veg: "Vegetarian",
  non_veg: "Non-vegetarian",
  egg: "Contains egg",
};

export interface StatusInfo {
  label: string;
  icon: string;
  tone: "new" | "active" | "ready" | "done" | "cancelled";
  hint: string;
}

// Status is always conveyed by text + icon, never by colour alone.
export const STATUS: Record<OrderStatus, StatusInfo> = {
  placed: { label: "Sent to kitchen", icon: "⏳", tone: "new", hint: "Waiting for the kitchen to accept" },
  accepted: { label: "Accepted", icon: "👍", tone: "active", hint: "The kitchen has your order" },
  preparing: { label: "Being prepared", icon: "🔥", tone: "active", hint: "Your food is being cooked" },
  ready: { label: "Ready", icon: "✅", tone: "ready", hint: "Coming to your table" },
  served: { label: "Served", icon: "🍽️", tone: "done", hint: "Enjoy your meal" },
  completed: { label: "Completed", icon: "✔️", tone: "done", hint: "Thank you!" },
  cancelled: { label: "Cancelled", icon: "✖️", tone: "cancelled", hint: "This order was cancelled" },
};

export function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

export function plural(count: number, one: string, many: string = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

export function retryText(seconds: number | undefined): string {
  if (!seconds || seconds < 60) return "Please wait a moment and try again.";
  return `Please try again in ${Math.ceil(seconds / 60)} minutes.`;
}
