// The staff password-reset page (/reset-password#token=…), opened from the emailed link.
import { createHash, randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import { API_URL, BACKEND_DIR, BACKEND_ENV, E2E_DB, OWNER_PASSWORD, PYTHON } from "./env.ts";

const NEW_PASSWORD = "Fresh-Start-2026-x";
let code: string;

/** A reset link as the API would email it: only the token's SHA-256 is stored. */
function resetToken(restaurantCode: string): string {
  const token = randomBytes(32).toString("base64url");
  const digest = createHash("sha256").update(token).digest("hex");
  const script = `
import asyncio, asyncpg, sys
async def main():
    conn = await asyncpg.connect("postgresql://restaurant_owner@127.0.0.1:54329/${E2E_DB}")
    await conn.execute(
        "INSERT INTO password_reset_tokens (restaurant_id, user_id, token_hash, expires_at) "
        "SELECT u.restaurant_id, u.id, $2, now() + interval '30 minutes' FROM users u "
        "JOIN restaurants r ON r.id = u.restaurant_id WHERE r.code = $1 AND u.username = 'owner'",
        sys.argv[1], bytes.fromhex(sys.argv[2]))
    await conn.close()
asyncio.run(main())
`;
  execFileSync(PYTHON, ["-c", script, restaurantCode, digest], { cwd: BACKEND_DIR, env: BACKEND_ENV });
  return token;
}

test.beforeAll(() => {
  code = `e2e-r-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  execFileSync(
    PYTHON,
    ["-m", "app.cli", "create-restaurant", "--code", code, "--name", "Reset Dhaba",
     "--owner-username", "owner", "--owner-name", "Owner", "--password-stdin"],
    { cwd: BACKEND_DIR, env: BACKEND_ENV, input: `${OWNER_PASSWORD}\n` },
  );
});

test("an emailed link sets a new password once", async ({ page, playwright }) => {
  const token = resetToken(code);
  await page.goto(`/reset-password#token=${token}`);
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  // The token leaves the address bar (history, screenshots, sharing) at once.
  await expect(page).toHaveURL(/\/reset-password$/);

  await page.getByLabel("New password").fill(NEW_PASSWORD);
  await page.getByLabel("Repeat it").fill(`${NEW_PASSWORD}?`);
  await page.getByRole("button", { name: "Save new password" }).click();
  await expect(page.getByRole("alert")).toHaveText("The two passwords are not the same.");

  await page.getByLabel("Repeat it").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Save new password" }).click();
  await expect(page.getByText("Your password is changed.")).toBeVisible();

  const api = await playwright.request.newContext({ baseURL: API_URL });
  const login = await api.post("/api/v1/auth/login", {
    data: { restaurant_code: code, username: "owner", password: NEW_PASSWORD },
  });
  expect(login.status()).toBe(200);
  await api.dispose();

  // The same link again (opened afresh from the email): refused, with a reason to act on.
  await page.goto("about:blank");
  await page.goto(`/reset-password#token=${token}`);
  await page.getByLabel("New password").fill("Another-Pass-2026-y");
  await page.getByLabel("Repeat it").fill("Another-Pass-2026-y");
  await page.getByRole("button", { name: "Save new password" }).click();
  await expect(page.getByRole("alert")).not.toBeEmpty();
  await expect(page.getByText("Your password is changed.")).toHaveCount(0);
});

test("a link without its token says what to do", async ({ page }) => {
  await page.goto("/reset-password");
  await expect(page.getByText("This link is not complete.")).toBeVisible();
});
