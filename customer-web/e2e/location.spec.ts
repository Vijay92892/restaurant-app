// Ordering without the PIN where the restaurant turned on its location check. Positions are
// emulated by the browser (Playwright's geolocation), exactly as a phone would report them.

import { execFileSync } from "node:child_process";

import { type APIRequestContext, type Page, expect, test } from "@playwright/test";

import { API_URL, BACKEND_DIR, BACKEND_ENV, OWNER_PASSWORD, PYTHON } from "./env.ts";

const AT_THE_RESTAURANT = { latitude: 28.2449519, longitude: 76.8135882, accuracy: 20 };
const FAR_AWAY = { latitude: 28.4595, longitude: 77.0266, accuracy: 20 }; // another town

interface Place {
  qr: string;
  pin: string;
}

async function created(api: APIRequestContext, headers: Record<string, string>, path: string, data: unknown) {
  const response = await api.post(`/api/v1${path}`, { headers, data });
  expect(response.status(), await response.text()).toBe(201);
  return response.json();
}

/** A restaurant with one dish and one table, its location check on (PIN backup as given). */
async function restaurant(playwright: import("@playwright/test").PlaywrightWorkerArgs["playwright"], fallback: boolean): Promise<Place> {
  const code = `loc-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  execFileSync(
    PYTHON,
    ["-m", "app.cli", "create-restaurant", "--code", code, "--name", "Highway Dhaba",
     "--owner-username", "owner", "--owner-name", "Owner", "--password-stdin"],
    { cwd: BACKEND_DIR, env: BACKEND_ENV, input: `${OWNER_PASSWORD}\n` },
  );
  const api = await playwright.request.newContext({ baseURL: API_URL });
  const login = await (
    await api.post("/api/v1/auth/login", { data: { restaurant_code: code, username: "owner", password: OWNER_PASSWORD } })
  ).json();
  const headers = { Authorization: `Bearer ${login.access_token}` };
  const drinks = await created(api, headers, "/menu/categories", { name: "Drinks", sort_order: 1 });
  await created(api, headers, "/menu/items", { category_id: drinks.id, name: "Masala Chai", dietary: "veg", display_price_paise: 4000 });
  const table = await created(api, headers, "/tables", { label: "T1" });
  const policy = await api.put("/api/v1/restaurant/customer-access", {
    headers,
    data: {
      location_ordering_enabled: true,
      latitude: AT_THE_RESTAURANT.latitude,
      longitude: AT_THE_RESTAURANT.longitude,
      radius_m: 100,
      pin_fallback_enabled: fallback,
    },
  });
  expect(policy.status(), await policy.text()).toBe(200);
  const pin = (await (await api.get("/api/v1/pin", { headers })).json()).pin as string;
  await api.dispose();
  return { qr: table.qr_url.split("/").pop(), pin };
}

async function at(page: Page, position: typeof AT_THE_RESTAURANT): Promise<void> {
  await page.context().grantPermissions(["geolocation"]);
  await page.context().setGeolocation(position);
}

let withPin: Place;
let locationOnly: Place;

test.beforeAll(async ({ playwright }) => {
  withPin = await restaurant(playwright, true);
  locationOnly = await restaurant(playwright, false);
});

test("a guest at the restaurant orders without the PIN", async ({ page }) => {
  await at(page, AT_THE_RESTAURANT);
  await page.goto(`/t/${withPin.qr}`);
  await expect(page.getByRole("heading", { name: "Order without a PIN" })).toBeVisible();
  await expect(page.getByText("within 100 m")).toBeVisible();
  await expect(page.getByLabel("Enter the table PIN")).toBeVisible(); // the backup is there too
  if (process.env.SCREENSHOTS) await page.screenshot({ path: "test-results/screens/7-location-check.png" });
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByRole("heading", { name: "Drinks" })).toBeVisible();

  await page.getByRole("button", { name: "Order Masala Chai" }).click();
  await page.getByRole("button", { name: /View order · 1 item/ }).click();
  await page.getByRole("dialog", { name: "Your order" }).getByRole("button", { name: "Place order" }).click();
  await expect(page.getByRole("heading", { name: "My orders" })).toBeVisible();
  await expect(page.locator(".order").first().getByText("Sent to kitchen")).toBeVisible();
});

test("elsewhere the check fails without saying how far, and the PIN still works", async ({ page }) => {
  await at(page, FAR_AWAY);
  await page.goto(`/t/${withPin.qr}`);
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByText("You don't seem to be at the restaurant. You can use the table PIN instead.")).toBeVisible();
  await expect(page.locator(".locate .error")).not.toHaveText(/\d/); // no distance given
  await page.getByLabel("Enter the table PIN").fill(withPin.pin);
  await expect(page.getByRole("heading", { name: "Drinks" })).toBeVisible();
});

test("a guest who doesn't share their location can use the PIN", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "only Chromium answers an ungranted request at once (denied)");
  await page.goto(`/t/${withPin.qr}`);
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByText(/^Location isn't allowed for this page\. .* You can use the table PIN instead\.$/)).toBeVisible();
  await page.getByLabel("Enter the table PIN").fill(withPin.pin);
  await expect(page.getByRole("heading", { name: "Drinks" })).toBeVisible();
});

test("without a PIN backup there is no PIN to try; staff take over", async ({ page }) => {
  await at(page, FAR_AWAY);
  await page.goto(`/t/${locationOnly.qr}`);
  await expect(page.getByRole("heading", { name: "Order without a PIN" })).toBeVisible();
  await expect(page.getByLabel("Enter the table PIN")).toHaveCount(0);
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByText("You don't seem to be at the restaurant. Please ask a member of staff to take your order.")).toBeVisible();
  // The PIN endpoint refuses too, even with the right PIN (the page is not the gatekeeper).
  const direct = await page.request.post(`/api/v1/public/tables/${locationOnly.qr}/session`, {
    headers: { Origin: new URL(page.url()).origin },
    data: { pin: locationOnly.pin },
  });
  expect(direct.status()).toBe(403);
});

test("the page never asks for a location until the guest taps", async ({ page }) => {
  await page.addInitScript(() => {
    const original = navigator.geolocation.getCurrentPosition.bind(navigator.geolocation);
    (window as unknown as { asked: number }).asked = 0;
    navigator.geolocation.getCurrentPosition = (...args: Parameters<Geolocation["getCurrentPosition"]>) => {
      (window as unknown as { asked: number }).asked += 1;
      return original(...args);
    };
  });
  await at(page, AT_THE_RESTAURANT);
  await page.goto(`/t/${withPin.qr}`);
  await expect(page.getByRole("button", { name: "Use my location" })).toBeVisible();
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => (window as unknown as { asked: number }).asked)).toBe(0);
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByRole("heading", { name: "Drinks" })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { asked: number }).asked)).toBe(1);
});
