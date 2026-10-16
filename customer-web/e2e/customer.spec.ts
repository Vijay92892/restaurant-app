import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { type APIRequestContext, expect, test } from "@playwright/test";

import { API_URL, BACKEND_DIR, BACKEND_ENV, OWNER_PASSWORD, PYTHON } from "./env.ts";

interface Fixture {
  name: string;
  qr: string;
  pin: string;
  soldOut: string; // id of a dish the kitchen marked sold out
}

let fx: Fixture;
const XSS_NAME = "<img src=x onerror=alert('xss')>";

async function created(api: APIRequestContext, headers: Record<string, string>, path: string, data: unknown) {
  const response = await api.post(`/api/v1${path}`, { headers, data });
  expect(response.status(), await response.text()).toBe(201);
  return response.json();
}

/** A fresh restaurant, created the way an operator would (CLI); returns an API session for it. */
async function newRestaurant(playwright: import("@playwright/test").PlaywrightWorkerArgs["playwright"], name: string) {
  const code = `e2e-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  execFileSync(
    PYTHON,
    ["-m", "app.cli", "create-restaurant", "--code", code, "--name", name,
     "--owner-username", "owner", "--owner-name", "Owner", "--password-stdin"],
    { cwd: BACKEND_DIR, env: BACKEND_ENV, input: `${OWNER_PASSWORD}\n` },
  );
  const api = await playwright.request.newContext({ baseURL: API_URL });
  const login = await (
    await api.post("/api/v1/auth/login", { data: { restaurant_code: code, username: "owner", password: OWNER_PASSWORD } })
  ).json();
  return { api, code, headers: { Authorization: `Bearer ${login.access_token}` } };
}

test.beforeAll(async ({ playwright }) => {
  const name = "E2E Dhaba";
  const { api, headers } = await newRestaurant(playwright, name);

  const starters = await created(api, headers, "/menu/categories", { name: "Starters", sort_order: 1 });
  const breads = await created(api, headers, "/menu/categories", { name: "Breads", sort_order: 2 });
  const paneer = await created(api, headers, "/menu/items", {
    category_id: starters.id, name: "Paneer Tikka", dietary: "veg", display_price_paise: 24900,
    description: "Char-grilled cottage cheese",
  });
  await created(api, headers, "/menu/items", { category_id: breads.id, name: "Butter Naan", dietary: "veg", display_price_paise: 6000 });
  await created(api, headers, "/menu/items", { category_id: breads.id, name: XSS_NAME });
  const roti = await created(api, headers, "/menu/items", { category_id: breads.id, name: "Missi Roti", dietary: "veg", display_price_paise: 4000 });
  const soldOut = await api.patch(`/api/v1/menu/items/${roti.id}/availability`, { headers, data: { is_available: false } });
  expect(soldOut.status()).toBe(200);
  const spice = await created(api, headers, "/menu/modifier-groups", { name: "Spice level", min_select: 1, max_select: 1 });
  for (const option of ["Regular", "Spicy", "Extra Spicy"]) {
    await created(api, headers, `/menu/modifier-groups/${spice.id}/modifiers`, { name: option });
  }
  const linked = await api.put(`/api/v1/menu/items/${paneer.id}/modifier-groups`, { headers, data: { group_ids: [spice.id] } });
  expect(linked.status()).toBe(200);
  const table = await created(api, headers, "/tables", { label: "T7" });
  const pin = (await (await api.get("/api/v1/pin", { headers })).json()).pin as string;
  fx = { name, qr: table.qr_url.split("/").pop(), pin, soldOut: roti.id };
  await api.dispose();
});

const wrongPin = (pin: string) => [...pin].map((d) => String((Number(d) + 1) % 10)).join("");

async function seat(page: import("@playwright/test").Page) {
  await page.goto(`/t/${fx.qr}`);
  await page.getByLabel("Enter the table PIN").fill(fx.pin);
  await expect(page.getByRole("heading", { name: "Starters" })).toBeVisible();
}

test("a diner goes from QR code to an order in the kitchen", async ({ page }) => {
  await page.goto(`/t/${fx.qr}`);
  await expect(page.getByRole("heading", { name: fx.name })).toBeVisible();
  await expect(page.getByText("Table T7")).toBeVisible();

  const pin = page.getByLabel("Enter the table PIN");
  await pin.fill(wrongPin(fx.pin)); // auto-submits when complete
  await expect(page.getByRole("alert")).toHaveText("That PIN didn't work. Please check it and try again.");
  await pin.fill(fx.pin);

  await expect(page.getByRole("heading", { name: "Starters" })).toBeVisible();
  await expect(page.getByText("₹249", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "Vegetarian" }).first()).toBeVisible();

  await page.getByRole("button", { name: "Add Paneer Tikka" }).click();
  const sheet = page.getByRole("dialog", { name: "Paneer Tikka" });
  await sheet.getByRole("button", { name: "Add to order" }).click();
  await expect(sheet.getByText("Please choose a spice level")).toBeVisible(); // required choice
  await sheet.getByRole("radio", { name: "Spicy", exact: true }).check();
  await sheet.getByLabel("Note for the kitchen (optional)").fill("less oil please");
  await sheet.getByRole("button", { name: "More" }).click();
  await sheet.getByRole("button", { name: "Add to order" }).click();
  await expect(page.getByRole("status")).toHaveText("Added 2 × Paneer Tikka");

  await page.getByRole("button", { name: "Add Butter Naan" }).click();
  await page.getByRole("button", { name: /View order · 3 items/ }).click();
  const cart = page.getByRole("dialog", { name: "Your order" });
  await expect(cart.getByText("Spice level: Spicy")).toBeVisible();
  await expect(cart.getByText("₹")).toHaveCount(0); // no totals or prices in the order
  await cart.getByRole("button", { name: "Place order" }).click();

  await expect(page.getByRole("heading", { name: "My orders" })).toBeVisible();
  const order = page.locator(".order").first();
  await expect(order.getByText(/Order #\d+/)).toBeVisible();
  await expect(order.getByText("Sent to kitchen")).toBeVisible();
  await expect(order.getByText("Paneer Tikka")).toBeVisible();
  await expect(order.getByText("“less oil please”")).toBeVisible();

  // Reloading keeps the table session (HttpOnly cookie) — no PIN needed again.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Starters" })).toBeVisible();
});

test("a double tap on Place order sends exactly one order, which can be cancelled", async ({ page }) => {
  await seat(page);
  await page.getByRole("button", { name: "Add Butter Naan" }).click();
  await page.getByRole("button", { name: /View order · 1 item/ }).click();
  await page.getByRole("dialog", { name: "Your order" }).getByRole("button", { name: "Place order" }).dblclick();
  await expect(page.getByRole("heading", { name: "My orders" })).toBeVisible();
  await expect(page.locator(".order")).toHaveCount(1);

  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Cancel order" }).click();
  await expect(page.locator(".order").first().getByText("Cancelled", { exact: true })).toBeVisible();
});

test("diners can search the menu and order from the results", async ({ page }) => {
  await seat(page);
  const search = page.getByRole("searchbox", { name: "Search the menu" });
  const sections = page.getByRole("navigation", { name: "Menu sections" });

  await search.fill("naan");
  await expect(page.getByText("Butter Naan")).toBeVisible();
  await expect(page.getByText("Paneer Tikka")).toBeHidden();
  await expect(page.getByRole("heading", { name: "Starters" })).toBeHidden();
  await expect(sections).toBeHidden();
  await expect(page.locator("#search-results")).toHaveText("1 dish found");

  await page.getByRole("button", { name: "Add Butter Naan" }).click();
  await expect(page.getByRole("button", { name: /View order · 1 item/ })).toBeVisible();
  await expect(search).toHaveValue("naan");

  await search.fill("panner"); // everyday spelling
  await expect(page.getByText("Paneer Tikka")).toBeVisible();

  await search.fill("pizza");
  await expect(page.getByText("No dishes match “pizza”.")).toBeVisible();
  await page.getByRole("button", { name: "Show the whole menu" }).click();
  await expect(search).toHaveValue("");
  await expect(page.getByRole("heading", { name: "Starters" })).toBeVisible();
  await expect(sections).toBeVisible();
});

test("section chips follow the menu and jump to a section", async ({ page }) => {
  await seat(page);
  const nav = page.getByRole("navigation", { name: "Menu sections" });
  await expect(nav.getByRole("button", { name: "Starters" })).toHaveAttribute("aria-current", "true");
  await nav.getByRole("button", { name: "Breads" }).click();
  await expect(nav.getByRole("button", { name: "Breads" })).toHaveAttribute("aria-current", "true");
  await expect(nav.getByRole("button", { name: "Starters" })).not.toHaveAttribute("aria-current", /.*/);
  const breads = page.getByRole("heading", { name: "Breads" });
  await expect(breads).toBeInViewport();
  await expect(breads).toBeFocused(); // keyboard and screen-reader users continue from there
});

test("sold-out dishes cannot be ordered, on the page or behind it", async ({ page }) => {
  await seat(page);
  const card = page.locator(".card", { hasText: "Missi Roti" });
  await expect(card.getByText("Sold out")).toBeVisible();
  await expect(card.getByRole("button", { name: "Add Missi Roti" })).toHaveCount(0);
  await expect(card.locator(".card-main")).toBeDisabled();
  // The server refuses it anyway (the page never decides what can be ordered).
  const attempt = await page.request.post("/api/v1/public/orders", {
    headers: { "Idempotency-Key": "sold-out-attempt-0001", Origin: new URL(page.url()).origin },
    data: { items: [{ menu_item_id: fx.soldOut, quantity: 1, modifier_ids: [] }] },
  });
  expect(attempt.status()).toBe(409);
});

test("the order sheet changes quantities and removes dishes", async ({ page }) => {
  await seat(page);
  const add = page.getByRole("button", { name: "Add Butter Naan" });
  await add.click();
  await add.click();
  await expect(page.locator(".card", { hasText: "Butter Naan" }).getByText("2 in order")).toBeVisible();
  await page.getByRole("button", { name: /View order · 2 items/ }).click();
  const cart = page.getByRole("dialog", { name: "Your order" });
  await expect(cart.getByText("2 items · Table T7")).toBeVisible();
  await cart.getByRole("button", { name: "One less Butter Naan" }).click();
  await expect(cart.getByText("1 item · Table T7")).toBeVisible();
  await cart.getByRole("button", { name: "Remove Butter Naan" }).click();
  await expect(cart).toBeHidden(); // empty: the sheet closes
  await expect(page.getByRole("button", { name: /View order/ })).toBeHidden();
  await expect(page.locator(".card", { hasText: "Butter Naan" }).locator(".in-cart")).toBeEmpty();
});

test("a restaurant's own theme and tagline dress the page; others keep the default", async ({ page, playwright }) => {
  const { api, code, headers } = await newRestaurant(playwright, "Heritage Dhaba");
  const file = path.join(os.tmpdir(), `branding-${code}.json`);
  writeFileSync(file, JSON.stringify({ branding: { theme: "heritage", tagline: "Since 1995" }, tables: ["T1"] }));
  execFileSync(PYTHON, ["-m", "app.cli", "apply-setup", "--code", code, "--file", file], { cwd: BACKEND_DIR, env: BACKEND_ENV });
  const [table] = await (await api.get("/api/v1/tables", { headers })).json();
  await api.dispose();

  await page.goto(`/t/${table.qr_url.split("/").pop()}`);
  await expect(page.getByRole("heading", { name: "Heritage Dhaba" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "heritage");
  await expect(page.getByText("Since 1995")).toBeVisible();
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#9e2025");
  const brand = await page.getByRole("heading", { name: "Heritage Dhaba" }).evaluate((el) => getComputedStyle(el).color);
  expect(brand).toBe("rgb(158, 32, 37)"); // heritage red

  await page.goto(`/t/${fx.qr}`);
  await expect(page.getByRole("heading", { name: fx.name })).toBeVisible();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
});

test("dish names are shown as text, never run as code", async ({ page }) => {
  let alerted = false;
  page.on("dialog", (dialog) => {
    alerted = true;
    void dialog.dismiss();
  });
  await seat(page);
  await expect(page.getByText(XSS_NAME, { exact: true })).toBeVisible();
  await expect(page.locator("img[src='x']")).toHaveCount(0);
  expect(alerted).toBe(false);
});

test("everything a restaurant or diner can type is shown as text, never run", async ({ page, playwright }) => {
  // Each name below is an attack that would run if it were ever treated as HTML.
  const hostile = {
    restaurant: "<script>alert('restaurant')</script>",
    table: "<b onclick=x>T1",
    category: "\"><svg onload=alert(1)>",
    dish: "<img src=x onerror=alert(2)>",
    description: "<iframe src=javascript:alert(3)>",
    group: "<details open ontoggle=alert(4)>",
    option: "<a href=javascript:alert(5)>x</a>",
    note: "<script>alert(6)</script>",
  };
  const { api, headers } = await newRestaurant(playwright, hostile.restaurant);
  const category = await created(api, headers, "/menu/categories", { name: hostile.category });
  const dish = await created(api, headers, "/menu/items", {
    category_id: category.id, name: hostile.dish, description: hostile.description,
  });
  const group = await created(api, headers, "/menu/modifier-groups", { name: hostile.group, min_select: 1, max_select: 1 });
  await created(api, headers, `/menu/modifier-groups/${group.id}/modifiers`, { name: hostile.option });
  expect((await api.put(`/api/v1/menu/items/${dish.id}/modifier-groups`, { headers, data: { group_ids: [group.id] } })).status()).toBe(200);
  const table = await created(api, headers, "/tables", { label: hostile.table });
  const pin = (await (await api.get("/api/v1/pin", { headers })).json()).pin as string;
  await api.dispose();

  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });
  await page.goto(`/t/${table.qr_url.split("/").pop()}`);
  await expect(page.getByRole("heading", { name: hostile.restaurant })).toBeVisible();
  await expect(page.getByText(`Table ${hostile.table}`)).toBeVisible();
  await page.getByLabel("Enter the table PIN").fill(pin);
  await expect(page.getByRole("heading", { name: hostile.category })).toBeVisible();

  await page.getByRole("button", { name: `Add ${hostile.dish}` }).click();
  const sheet = page.getByRole("dialog", { name: hostile.dish });
  await expect(sheet.getByText(hostile.description)).toBeVisible();
  await expect(sheet.getByText(hostile.group)).toBeVisible();
  await sheet.getByRole("radio", { name: hostile.option }).check();
  await sheet.getByLabel("Note for the kitchen (optional)").fill(hostile.note);
  await sheet.getByRole("button", { name: "Add to order" }).click();
  await page.getByRole("button", { name: /View order · 1 item/ }).click();
  await page.getByRole("dialog", { name: "Your order" }).getByRole("button", { name: "Place order" }).click();
  await expect(page.getByRole("heading", { name: "My orders" })).toBeVisible();
  await expect(page.locator(".order").first().getByText(`“${hostile.note}”`)).toBeVisible();

  // Nothing was parsed as markup and nothing ran.
  const injected = await page.locator("iframe, details, svg, img[src='x'], a[href^='javascript'], b[onclick]").count();
  expect(injected).toBe(0);
  expect(await page.locator("script").count()).toBe(1); // only the app's own bundle
  expect(dialogs).toEqual([]);
});

test("someone with only a photo of the QR code cannot see the menu or order", async ({ page }) => {
  await page.goto(`/t/${fx.qr}`);
  await expect(page.getByLabel("Enter the table PIN")).toBeVisible();
  const menu = await page.request.get("/api/v1/public/menu");
  expect(menu.status()).toBe(401);
  const order = await page.request.post("/api/v1/public/orders", {
    headers: { "Idempotency-Key": "photo-attack-0001" },
    data: { items: [] },
  });
  expect(order.status()).toBe(401);
});

test("the first screen loads quickly on a weak 4G connection", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "network throttling uses the Chrome DevTools protocol");
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
  // ~1.6 Mbps down, 750 kbps up, 150 ms round trips.
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: 150,
    downloadThroughput: (1.6 * 1024 * 1024) / 8,
    uploadThroughput: (750 * 1024) / 8,
  });
  const started = Date.now();
  await page.goto(`/t/${fx.qr}`);
  await expect(page.getByLabel("Enter the table PIN")).toBeVisible();
  const elapsed = Date.now() - started;
  console.log(`PIN screen ready after ${elapsed} ms on emulated weak 4G (cold cache)`);
  expect(elapsed).toBeLessThan(2500);
});
