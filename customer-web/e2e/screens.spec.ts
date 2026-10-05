// Visual snapshots of each screen for review: `SCREENSHOTS=1 npx playwright test screens`.
// Skipped in normal runs. Images land in test-results/screens/.

import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import { API_URL, BACKEND_DIR, BACKEND_ENV, OWNER_PASSWORD, PYTHON } from "./env.ts";

test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture screens");

test("capture every customer screen", async ({ page, playwright }) => {
  const code = `shots-${Date.now().toString(36)}`;
  execFileSync(
    PYTHON,
    ["-m", "app.cli", "create-restaurant", "--code", code, "--name", "Spice Garden",
     "--owner-username", "owner", "--owner-name", "Owner", "--password-stdin"],
    { cwd: BACKEND_DIR, env: BACKEND_ENV, input: `${OWNER_PASSWORD}\n` },
  );
  const api = await playwright.request.newContext({ baseURL: API_URL });
  const token = (await (await api.post("/api/v1/auth/login", {
    data: { restaurant_code: code, username: "owner", password: OWNER_PASSWORD },
  })).json()).access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const post = async (path: string, data: unknown) => (await api.post(`/api/v1${path}`, { headers, data })).json();

  const starters = await post("/menu/categories", { name: "Starters", sort_order: 1 });
  const mains = await post("/menu/categories", { name: "Main course", sort_order: 2 });
  const breads = await post("/menu/categories", { name: "Breads", sort_order: 3 });
  const paneer = await post("/menu/items", { category_id: starters.id, name: "Paneer Tikka", dietary: "veg", display_price_paise: 24900, description: "Cottage cheese marinated in yoghurt and spices, char-grilled in the tandoor." });
  await post("/menu/items", { category_id: starters.id, name: "Chicken 65", dietary: "non_veg", display_price_paise: 27900, description: "Crispy, spicy fried chicken with curry leaves." });
  await post("/menu/items", { category_id: starters.id, name: "Egg Bhurji", dietary: "egg", display_price_paise: 14900 });
  const dal = await post("/menu/items", { category_id: mains.id, name: "Dal Makhani", dietary: "veg", display_price_paise: 22900, description: "Black lentils slow-cooked overnight with butter and cream." });
  await post("/menu/items", { category_id: mains.id, name: "Butter Chicken", dietary: "non_veg", display_price_paise: 32900 });
  await post("/menu/items", { category_id: breads.id, name: "Butter Naan", dietary: "veg", display_price_paise: 6000 });
  const roti = await post("/menu/items", { category_id: breads.id, name: "Tandoori Roti", dietary: "veg", display_price_paise: 3000 });
  await api.patch(`/api/v1/menu/items/${roti.id}/availability`, { headers, data: { is_available: false } });
  const spice = await post("/menu/modifier-groups", { name: "Spice level", min_select: 1, max_select: 1 });
  for (const name of ["Mild", "Medium", "Hot"]) await post(`/menu/modifier-groups/${spice.id}/modifiers`, { name });
  const extras = await post("/menu/modifier-groups", { name: "Extras", min_select: 0, max_select: 2 });
  await post(`/menu/modifier-groups/${extras.id}/modifiers`, { name: "Extra cheese", display_price_delta_paise: 4000 });
  await post(`/menu/modifier-groups/${extras.id}/modifiers`, { name: "Mint chutney", display_price_delta_paise: 2000 });
  await api.put(`/api/v1/menu/items/${paneer.id}/modifier-groups`, { headers, data: { group_ids: [spice.id, extras.id] } });
  await api.put(`/api/v1/menu/items/${dal.id}/modifier-groups`, { headers, data: { group_ids: [spice.id] } });
  const table = await post("/tables", { label: "T4" });
  const pin = (await (await api.get("/api/v1/pin", { headers })).json()).pin as string;
  const qr = table.qr_url.split("/").pop();

  const shot = (name: string) => page.screenshot({ path: `test-results/screens/${name}.png` });
  await page.goto(`/t/${qr}`);
  await expect(page.getByLabel("Enter the table PIN")).toBeVisible();
  await shot("1-pin");
  await page.getByLabel("Enter the table PIN").fill(pin);
  await expect(page.getByRole("heading", { name: "Starters" })).toBeVisible();
  await shot("2-menu");
  await page.getByRole("button", { name: "Add Paneer Tikka" }).click();
  const sheet = page.getByRole("dialog", { name: "Paneer Tikka" });
  await sheet.getByRole("radio", { name: "Medium" }).check();
  await sheet.getByRole("checkbox", { name: /Extra cheese/ }).check();
  await shot("3-dish-options");
  await sheet.getByRole("button", { name: "Add to order" }).click();
  await page.getByRole("button", { name: "Add Butter Naan" }).click();
  await page.getByRole("button", { name: "Add Butter Naan" }).click();
  await page.getByRole("button", { name: /View order/ }).click();
  await shot("4-cart");
  await page.getByRole("dialog", { name: "Your order" }).getByRole("button", { name: "Place order" }).click();
  await expect(page.getByRole("heading", { name: "My orders" })).toBeVisible();
  await page.waitForTimeout(400);
  await shot("5-orders");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.getByRole("button", { name: "← Menu" }).click();
  await expect(page.getByRole("heading", { name: "Starters" })).toBeVisible();
  await shot("6-menu-dark");
  await api.dispose();
});
