import { readFileSync } from "node:fs";

import { defineConfig, type Plugin } from "vite";

// Same origin as the API in every environment: in development Vite proxies /api and /media to
// the backend; in production one reverse proxy serves both (so the session cookie is first-party).
const API_TARGET = process.env.API_TARGET ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: API_TARGET, changeOrigin: false },
  "/media": { target: API_TARGET, changeOrigin: false },
};

// End-to-end tests serve the built app over HTTPS with a throwaway certificate (Safari/WebKit
// only stores the Secure session cookie over https, as in production).
const certDir = process.env.PREVIEW_CERT_DIR;
const previewHttps = certDir
  ? { key: readFileSync(`${certDir}/key.pem`), cert: readFileSync(`${certDir}/cert.pem`) }
  : undefined;

// The preview (end-to-end tests) sends production's Permissions-Policy, read from the real
// Caddyfile, so the location tests fail if production would block the location prompt. The
// Docker build has no deploy/ folder beside the page; it doesn't need this.
function productionPermissionsPolicy(): Record<string, string> {
  try {
    const caddyfile = readFileSync(new URL("../deploy/Caddyfile", import.meta.url), "utf8");
    const policy = /Permissions-Policy\s+"([^"]*)"/.exec(caddyfile)?.[1];
    return policy ? { "Permissions-Policy": policy } : {};
  } catch {
    return {};
  }
}

// Strict CSP for production builds only (the dev server injects inline styles for HMR).
// Everything — script, style, images, API calls — comes from our own origin. Data is only ever
// rendered with textContent, so this is defence in depth against XSS.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
].join("; ");

function productionCsp(): Plugin {
  return {
    name: "production-csp",
    apply: "build",
    transformIndexHtml: (html) =>
      html.replace(
        "<!-- CSP -->",
        `<meta http-equiv="Content-Security-Policy" content="${CSP}" />`,
      ),
  };
}

export default defineConfig({
  plugins: [productionCsp()],
  appType: "spa", // /t/<token> falls back to index.html
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 4173, strictPort: true, proxy, https: previewHttps, headers: productionPermissionsPolicy() },
  build: {
    target: "es2020",
    cssCodeSplit: false,
    assetsInlineLimit: 0,
    sourcemap: false,
  },
});
