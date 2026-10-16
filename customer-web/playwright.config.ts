import { defineConfig, devices } from "@playwright/test";

import { API_PORT, API_URL, BACKEND_DIR, BACKEND_ENV, CERT_DIR, E2E_DB, PYTHON, WEB_PORT, WEB_URL } from "./e2e/env.ts";

// Three browser engines, each as a phone: Chrome (Android), WebKit (Safari on iPhone) and
// Firefox. Chrome is the installed Google Chrome; WebKit and Firefox come from
// `npx playwright install webkit firefox`. Screenshots are taken in Chrome only.
// Each project is a different phone to the API (documentation addresses, RFC 5737): the
// preview proxy is trusted like Caddy in production (TRUSTED_PROXIES in e2e/env.ts).
const phone = (n: number) => ({ "X-Forwarded-For": `198.51.100.${n}` });

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: WEB_URL,
    ignoreHTTPSErrors: true, // the throwaway localhost certificate
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chrome", use: { ...devices["Pixel 7"], channel: "chrome", extraHTTPHeaders: phone(11) } },
    { name: "webkit", use: { ...devices["iPhone 14"], extraHTTPHeaders: phone(12) }, testIgnore: /screens\.spec/ },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"], viewport: { width: 412, height: 915 }, extraHTTPHeaders: phone(13) },
      testIgnore: /screens\.spec/,
    },
  ],
  webServer: [
    {
      // Starts (or reuses) the local dev Postgres with a separate e2e database, then the API.
      command: `"${PYTHON}" scripts/dev_db.py --database ${E2E_DB} --no-env && "${PYTHON}" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port ${API_PORT}`,
      cwd: BACKEND_DIR,
      url: `${API_URL}/readyz`,
      env: BACKEND_ENV,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      // The production build (with its CSP), served over HTTPS with /api proxied to the backend.
      command: `"${PYTHON}" e2e/make_cert.py "${CERT_DIR}" && npx vite build && npx vite preview --port ${WEB_PORT} --strictPort`,
      url: WEB_URL,
      ignoreHTTPSErrors: true,
      env: { ...(process.env as Record<string, string>), API_TARGET: API_URL, PREVIEW_CERT_DIR: CERT_DIR },
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
