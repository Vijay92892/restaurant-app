// Shared settings for the end-to-end run: a dedicated database (`restaurant_e2e`) on the local
// dev Postgres, a backend on :8010 and the production build of this app on :4173.

import path from "node:path";
import { fileURLToPath } from "node:url";

export const BACKEND_DIR = fileURLToPath(new URL("../../backend/", import.meta.url));
export const PYTHON =
  process.platform === "win32"
    ? path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
    : path.join(BACKEND_DIR, ".venv", "bin", "python");

export const API_PORT = 8010;
export const WEB_PORT = 4173;
export const API_URL = `http://127.0.0.1:${API_PORT}`;
// HTTPS, like production: Safari/WebKit refuses the Secure session cookie over http.
export const WEB_URL = `https://localhost:${WEB_PORT}`;
export const CERT_DIR = fileURLToPath(new URL("./.cert", import.meta.url));
export const E2E_DB = "restaurant_e2e";
export const OWNER_PASSWORD = "E2e-Dhaba-Pass-2026"; // must not contain the username

const DB = `127.0.0.1:54329/${E2E_DB}`;

export const BACKEND_ENV: Record<string, string> = {
  ...(process.env as Record<string, string>),
  ENV: "development",
  DATABASE_URL: `postgresql+asyncpg://restaurant_app@${DB}`,
  MIGRATION_DATABASE_URL: `postgresql+asyncpg://restaurant_owner@${DB}`,
  APP_DB_ROLE: "restaurant_app",
  PIN_SCHEDULER_ENABLED: "false",
  REGISTRATION_MODE: "closed",
  PUBLIC_ORDER_BASE_URL: WEB_URL,
  PUBLIC_WEB_ORIGINS: WEB_URL, // exercise the production Origin check too
  // The local preview proxy stands in for Caddy: each browser project then reaches the API as
  // its own phone (X-Forwarded-For, playwright.config.ts), so the per-address limits of
  // production apply per project instead of adding up across the whole run.
  TRUSTED_PROXIES: "127.0.0.1/32,::1/128",
  MEDIA_ROOT: path.join(BACKEND_DIR, ".media-e2e"),
  LOG_LEVEL: "WARNING",
};
