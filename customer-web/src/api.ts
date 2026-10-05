// Same-origin API client. The table-session cookie (HttpOnly) is sent automatically; this code
// never sees or stores it.

import type { Landing, Order, OrderRequest, PublicMenu, SessionInfo } from "./types.ts";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | undefined;
  readonly menuItemId: string | undefined;

  constructor(status: number, code: string, message: string, retryAfter?: number, menuItemId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
    this.menuItemId = menuItemId;
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

interface Conditional<T> {
  data: T | null; // null ⇒ 304 Not Modified
  etag: string | null;
  status: number;
}

const BASE = "/api/v1/public";

async function call<T>(
  method: string,
  path: string,
  options: { body?: unknown; headers?: Record<string, string>; etag?: string | null } = {},
): Promise<Conditional<T>> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.etag) headers["If-None-Match"] = options.etag;
  let response: Response;
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store", // we manage ETags ourselves
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "network", "No internet connection. Please try again.");
  }
  const etag = response.headers.get("ETag");
  if (response.status === 304) return { data: null, etag, status: 304 };
  let payload: unknown = null;
  if (response.status !== 204) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const error = (payload as { error?: Record<string, unknown> } | null)?.error ?? {};
    const retry = Number(response.headers.get("Retry-After") ?? "") || undefined;
    throw new ApiError(
      response.status,
      typeof error.code === "string" ? error.code : "error",
      typeof error.message === "string" ? error.message : "Something went wrong. Please try again.",
      retry,
      typeof error.menu_item_id === "string" ? error.menu_item_id : undefined,
    );
  }
  return { data: payload as T, etag, status: response.status };
}

const qr = (token: string) => `/tables/${encodeURIComponent(token)}`;

export const api = {
  landing: async (token: string) => (await call<Landing>("GET", qr(token))).data as Landing,
  openSession: async (token: string, pin: string) =>
    (await call<SessionInfo>("POST", `${qr(token)}/session`, { body: { pin } })).data as SessionInfo,
  leave: async () => {
    await call<null>("DELETE", "/session");
  },
  menu: (etag: string | null) => call<PublicMenu>("GET", "/menu", { etag }),
  orders: (etag: string | null) => call<Order[]>("GET", "/orders", { etag }),
  placeOrder: (request: OrderRequest, idempotencyKey: string) =>
    call<Order>("POST", "/orders", { body: request, headers: { "Idempotency-Key": idempotencyKey } }),
  cancelOrder: async (orderId: string) =>
    (await call<Order>("POST", `/orders/${encodeURIComponent(orderId)}/cancel`, { body: {} })).data as Order,
};
