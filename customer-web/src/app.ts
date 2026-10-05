// Customer ordering app: QR → PIN → menu → cart → order → live status.
// Everything the server says is rendered as text (see dom.ts). The server re-validates every
// order; client-side checks exist only to give quick, friendly feedback.

import { ApiError, api } from "./api.ts";
import {
  type Cart,
  type CartLine,
  MAX_LINE_NOTES,
  MAX_LINE_QUANTITY,
  MAX_ORDER_NOTES,
  addLine,
  cartFingerprint,
  emptyCart,
  parseStoredCart,
  removeItems,
  setQuantity,
  toOrderRequest,
  totalQuantity,
} from "./cart.ts";
import { type Child, h, mount } from "./dom.ts";
import { DIETARY_LABEL, STATUS, formatDelta, formatPrice, formatTime, plural, retryText } from "./format.ts";
import { defaultSelection, groupError, groupRule, toggleOption } from "./options.ts";
import { newIdempotencyKey, storage } from "./storage.ts";
import type { Landing, MenuGroup, MenuItem, Order, PublicMenu } from "./types.ts";

const TOKEN_PATTERN = /^\/t\/([A-Za-z0-9_-]{16,64})\/?$/;
const ORDERS_POLL_MS = 10_000;
const MENU_POLL_MS = 60_000;
const ACTIVE: ReadonlySet<string> = new Set(["placed", "accepted", "preparing", "ready"]);

type View = "pin" | "menu" | "orders";

interface State {
  token: string;
  landing: Landing;
  view: View;
  menu: PublicMenu | null;
  menuEtag: string | null;
  orders: Order[];
  ordersEtag: string | null;
  cart: Cart;
  placing: boolean;
}

let state: State;
let root: HTMLElement;
let pollTimer: number | undefined;
const toastRegion = h("div", { class: "toast", attrs: { role: "status", "aria-live": "polite" } });

const cartKey = () => `cart:${state.token}`;
const pendingKey = () => `order-key:${state.token}`;

// --- bootstrap -----------------------------------------------------------------------------------

export async function start(element: HTMLElement): Promise<void> {
  root = element;
  const match = TOKEN_PATTERN.exec(location.pathname);
  if (!match?.[1]) {
    showMessage("Scan the QR code on your table to see the menu.");
    return;
  }
  let landing: Landing;
  try {
    landing = await api.landing(match[1]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      showMessage("This QR code isn't active. Please ask a member of staff for help.");
    } else {
      showMessage(errorText(error), () => void start(element));
    }
    return;
  }
  state = {
    token: match[1],
    landing,
    view: "pin",
    menu: null,
    menuEtag: null,
    orders: [],
    ordersEtag: null,
    cart: parseStoredCart(storage.get(`cart:${match[1]}`)),
    placing: false,
  };
  document.title = `${landing.restaurant_name} · Table ${landing.table_label}`;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void refresh();
  });
  if (landing.has_session) await showMenu();
  else showPin();
}

function showMessage(text: string, retry?: () => void): void {
  mount(
    root,
    h("main", { class: "message" },
      h("p", { text }),
      retry ? h("button", { class: "primary", text: "Try again", on: { click: retry } }) : null,
    ),
  );
}

// --- helpers -------------------------------------------------------------------------------------

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return `Too many attempts. ${retryText(error.retryAfter)}`;
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

function toast(text: string): void {
  toastRegion.textContent = text;
  toastRegion.classList.add("visible");
  window.setTimeout(() => toastRegion.classList.remove("visible"), 3500);
}

function saveCart(): void {
  storage.set(cartKey(), JSON.stringify(state.cart));
  updateCartBar();
}

function sessionEnded(error: unknown): boolean {
  if (error instanceof ApiError && error.code === "table_session_required") {
    stopPolling();
    showPin("Your table session has ended. Please enter the table PIN again.");
    return true;
  }
  return false;
}

function stopPolling(): void {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
  pollTimer = undefined;
}

function startPolling(): void {
  stopPolling();
  pollTimer = window.setInterval(
    () => {
      if (document.visibilityState === "visible") void refresh();
    },
    state.view === "orders" ? ORDERS_POLL_MS : MENU_POLL_MS,
  );
}

async function refresh(): Promise<void> {
  try {
    if (state.view === "orders") {
      if (await loadOrders()) renderOrderList();
    } else if (state.view === "menu") {
      if (await loadMenu()) renderMenu();
    }
  } catch (error) {
    sessionEnded(error); // otherwise a missed poll is harmless
  }
}

function topBar(): HTMLElement {
  const active = state.orders.filter((o) => ACTIVE.has(o.status)).length;
  return h("header", { class: "topbar" },
    h("div", { class: "where" },
      h("strong", { text: state.landing.restaurant_name }),
      h("span", { text: `Table ${state.landing.table_label}` }),
    ),
    state.view === "orders"
      ? h("button", { class: "ghost", text: "← Menu", on: { click: () => void showMenu() } })
      : h("button", {
          class: "ghost",
          attrs: { "aria-label": active ? `My orders, ${active} in progress` : "My orders" },
          on: { click: () => void showOrders() },
        }, "My orders", active ? h("span", { class: "badge", text: String(active) }) : null),
  );
}

// --- PIN -----------------------------------------------------------------------------------------

function showPin(notice?: string): void {
  state.view = "pin";
  const { pin_length: length } = state.landing;
  const error = h("p", { class: "error", attrs: { role: "alert" }, text: notice ?? "" });
  const input = h("input", {
    class: "pin-input",
    attrs: {
      id: "pin",
      name: "pin",
      inputmode: "numeric",
      autocomplete: "one-time-code",
      pattern: "[0-9]*",
      maxlength: length,
      "aria-describedby": "pin-help",
      required: true,
    },
  });
  const submit = h("button", { class: "primary", attrs: { type: "submit" }, text: "Continue" });

  const send = async () => {
    if (input.value.length !== length || submit.disabled) return;
    submit.disabled = true;
    submit.textContent = "Checking…";
    error.textContent = "";
    try {
      await api.openSession(state.token, input.value);
      await showMenu();
    } catch (failure) {
      error.textContent =
        failure instanceof ApiError && failure.status === 401
          ? "That PIN didn't work. Please check it and try again."
          : errorText(failure);
      input.value = "";
      input.focus();
    } finally {
      submit.disabled = false;
      submit.textContent = "Continue";
    }
  };

  input.addEventListener("input", () => {
    input.value = input.value.replace(/\D/g, "").slice(0, length);
    if (input.value.length === length) void send();
  });
  const form = h("form", { class: "pin-form", attrs: { novalidate: true } },
    h("label", { attrs: { for: "pin" }, text: "Enter the table PIN" }),
    h("p", { class: "help", attrs: { id: "pin-help" }, text: `Ask your waiter for the ${length}-digit PIN, or check the card on your table.` }),
    input,
    error,
    submit,
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void send();
  });
  mount(root,
    h("main", { class: "pin-screen" },
      h("h1", { text: state.landing.restaurant_name }),
      h("p", { class: "table", text: `Table ${state.landing.table_label}` }),
      form,
    ),
    toastRegion,
  );
  input.focus();
}

// --- menu ----------------------------------------------------------------------------------------

async function loadMenu(): Promise<boolean> {
  const result = await api.menu(state.menuEtag);
  if (!result.data) return false;
  state.menu = result.data;
  state.menuEtag = result.etag;
  // Drop cart lines whose dish disappeared or can no longer be ordered.
  const orderable = new Set(state.menu.categories.flatMap((c) => c.items).filter((i) => i.orderable).map((i) => i.id));
  const gone = new Set(state.cart.lines.map((l) => l.itemId).filter((id) => !orderable.has(id)));
  if (gone.size) {
    state.cart = removeItems(state.cart, gone);
    saveCart();
    toast("Some dishes in your order are no longer available and were removed.");
  }
  return true;
}

async function showMenu(): Promise<void> {
  state.view = "menu";
  try {
    await loadMenu();
    await loadOrders().catch(() => false); // badge only; not critical
  } catch (error) {
    if (!sessionEnded(error)) showMessage(errorText(error), () => void showMenu());
    return;
  }
  renderMenu();
  startPolling();
}

const groupsById = (): Map<string, MenuGroup> =>
  new Map((state.menu?.modifier_groups ?? []).map((g) => [g.id, g]));

function dietMark(item: MenuItem): Child {
  if (!item.dietary) return null;
  return h("span", { class: `diet ${item.dietary}`, attrs: { role: "img", "aria-label": DIETARY_LABEL[item.dietary], title: DIETARY_LABEL[item.dietary] } });
}

let cartBar: HTMLButtonElement | null = null;

function updateCartBar(): void {
  if (!cartBar) return;
  const count = totalQuantity(state.cart);
  cartBar.hidden = count === 0 || state.view !== "menu";
  cartBar.replaceChildren(
    h("span", { text: `View order · ${plural(count, "item")}` }),
    h("span", { attrs: { "aria-hidden": "true" }, text: "→" }),
  );
}

function renderMenu(): void {
  const menu = state.menu;
  if (!menu) return;
  const groups = groupsById();
  const sections = menu.categories.map((category) =>
    h("section", { class: "category", attrs: { id: `cat-${category.id}`, "aria-labelledby": `h-${category.id}` } },
      h("h2", { attrs: { id: `h-${category.id}` }, text: category.name }),
      category.description ? h("p", { class: "category-desc", text: category.description }) : null,
      h("ul", { class: "items" }, ...category.items.map((item) => itemCard(item, groups))),
    ),
  );
  const chips = h("nav", { class: "cats", attrs: { "aria-label": "Menu sections" } },
    ...menu.categories.map((category) =>
      h("button", {
        class: "chip",
        text: category.name,
        on: { click: () => document.getElementById(`cat-${category.id}`)?.scrollIntoView({ behavior: "smooth" }) },
      }),
    ),
  );
  cartBar = h("button", { class: "cartbar", on: { click: () => openCart() } });
  mount(root,
    topBar(),
    chips,
    h("main", { class: "menu" },
      ...(sections.length
        ? sections
        : [h("p", { class: "empty", text: "The menu is being updated. Please ask your waiter." })]),
    ),
    cartBar,
    toastRegion,
  );
  updateCartBar();
}

function itemCard(item: MenuItem, groups: Map<string, MenuGroup>): HTMLElement {
  const hasOptions = item.modifier_group_ids.some((id) => groups.has(id));
  const status = !item.is_available ? "Sold out" : !item.orderable ? "Unavailable" : null;
  const add = status
    ? h("span", { class: "soldout", text: status })
    : h("button", {
        class: "add",
        attrs: { "aria-label": `Add ${item.name}` },
        text: hasOptions ? "Choose" : "Add",
        on: {
          click: (event) => {
            event.stopPropagation();
            if (hasOptions) openItem(item);
            else quickAdd(item);
          },
        },
      });
  return h("li", { class: `item${status ? " disabled" : ""}` },
    h("button", {
      class: "item-main",
      attrs: { "aria-label": `${item.name}${status ? `, ${status}` : ""}`, disabled: !!status },
      on: { click: () => openItem(item) },
    },
      h("span", { class: "item-title" }, dietMark(item), h("span", { class: "name", text: item.name })),
      item.description ? h("span", { class: "desc", text: item.description }) : null,
      item.display_price_paise !== null ? h("span", { class: "price", text: formatPrice(item.display_price_paise) }) : null,
    ),
    h("div", { class: "item-side" },
      item.thumbnail_url
        ? h("img", { attrs: { src: item.thumbnail_url, alt: "", loading: "lazy", decoding: "async", width: 88, height: 88 } })
        : null,
      add,
    ),
  );
}

function quickAdd(item: MenuItem): void {
  const result = addLine(state.cart, {
    itemId: item.id, name: item.name, quantity: 1, modifierIds: [], modifierLabels: [], notes: "",
  });
  if (!result.ok) {
    toast("Your order is full. Please place it first, then order more.");
    return;
  }
  state.cart = result.cart;
  saveCart();
  toast(`Added ${item.name}`);
}

// --- dish sheet ----------------------------------------------------------------------------------

function sheet(title: string, ...content: Child[]): HTMLDialogElement {
  const dialog = h("dialog", { class: "sheet", attrs: { "aria-label": title } },
    h("div", { class: "sheet-head" },
      h("h2", { text: title }),
      h("button", { class: "close", attrs: { "aria-label": "Close" }, text: "✕", on: { click: () => dialog.close() } }),
    ),
    ...content,
  );
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close(); // tap on the backdrop
  });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

function openItem(item: MenuItem): void {
  if (!item.orderable) return;
  const groups = item.modifier_group_ids.map((id) => groupsById().get(id)).filter((g): g is MenuGroup => !!g);
  const selections = new Map(groups.map((g) => [g.id, defaultSelection(g)]));
  let quantity = 1;
  const errors = new Map<string, HTMLElement>();

  const groupFieldset = (group: MenuGroup): HTMLElement => {
    const error = h("p", { class: "error", attrs: { role: "alert" } });
    errors.set(group.id, error);
    const single = group.max_select === 1;
    const inputs = group.options.map((option) => {
      const input = h("input", {
        attrs: {
          type: single ? "radio" : "checkbox",
          name: `g-${group.id}`,
          value: option.id,
          disabled: !option.is_available,
          checked: selections.get(group.id)?.includes(option.id) ?? false,
        },
      });
      input.addEventListener("change", () => {
        const next = toggleOption(group, selections.get(group.id) ?? [], option.id);
        selections.set(group.id, next);
        for (const box of fieldset.querySelectorAll("input")) box.checked = next.includes(box.value);
        error.textContent = "";
      });
      return h("label", { class: `option${option.is_available ? "" : " off"}` },
        input,
        h("span", { class: "option-name", text: option.name }),
        h("span", { class: "option-extra", text: option.is_available ? formatDelta(option.display_price_delta_paise) : "Not available" }),
      );
    });
    const fieldset = h("fieldset", { class: "group" },
      h("legend", {}, h("span", { text: group.name }), h("small", { text: groupRule(group) })),
      ...inputs,
      error,
    );
    return fieldset;
  };

  const notes = h("input", {
    class: "notes",
    attrs: { type: "text", maxlength: MAX_LINE_NOTES, placeholder: "e.g. less oil, no onion", "aria-label": "Note for the kitchen (optional)" },
  });
  const qty = h("output", { class: "qty-value", text: "1", attrs: { "aria-live": "polite" } });
  const step = (delta: number) => {
    quantity = Math.max(1, Math.min(MAX_LINE_QUANTITY, quantity + delta));
    qty.textContent = String(quantity);
  };
  const add = h("button", { class: "primary wide", text: "Add to order" });
  add.addEventListener("click", () => {
    let valid = true;
    for (const group of groups) {
      const message = groupError(group, selections.get(group.id) ?? []);
      const holder = errors.get(group.id);
      if (holder) holder.textContent = message ?? "";
      if (message) valid = false;
    }
    if (!valid) return;
    const chosen = groups.flatMap((g) => (selections.get(g.id) ?? []).map((id) => ({ g, o: g.options.find((o) => o.id === id) })));
    const result = addLine(state.cart, {
      itemId: item.id,
      name: item.name,
      quantity,
      modifierIds: chosen.map(({ o }) => o?.id ?? "").filter(Boolean),
      modifierLabels: chosen.map(({ g, o }) => `${g.name}: ${o?.name ?? ""}`),
      notes: notes.value,
    });
    if (!result.ok) {
      toast("Your order is full. Please place it first, then order more.");
      return;
    }
    state.cart = result.cart;
    saveCart();
    dialog.close();
    toast(quantity === 1 ? `Added ${item.name}` : `Added ${quantity} × ${item.name}`);
  });

  const dialog = sheet(item.name,
    item.image_url ? h("img", { class: "hero", attrs: { src: item.image_url, alt: "", decoding: "async" } }) : null,
    h("div", { class: "sheet-body" },
      h("p", { class: "item-title" }, dietMark(item), item.display_price_paise !== null ? h("span", { class: "price", text: formatPrice(item.display_price_paise) }) : null),
      item.description ? h("p", { class: "desc", text: item.description }) : null,
      ...groups.map(groupFieldset),
      h("label", { class: "field" }, h("span", { text: "Note for the kitchen (optional)" }), notes),
    ),
    h("div", { class: "sheet-foot" },
      h("div", { class: "stepper" },
        h("button", { attrs: { "aria-label": "Fewer" }, text: "−", on: { click: () => step(-1) } }),
        qty,
        h("button", { attrs: { "aria-label": "More" }, text: "+", on: { click: () => step(1) } }),
      ),
      add,
    ),
  );
}

// --- cart ----------------------------------------------------------------------------------------

function openCart(): void {
  const message = h("p", { class: "error", attrs: { role: "alert" } });
  const list = h("ul", { class: "cart-lines" });
  const notes = h("textarea", {
    attrs: { maxlength: MAX_ORDER_NOTES, rows: 2, placeholder: "Anything else the kitchen should know?", "aria-label": "Note for the whole order (optional)" },
  });
  notes.value = state.cart.notes;
  notes.addEventListener("input", () => {
    state.cart = { ...state.cart, notes: notes.value };
    saveCart();
  });
  const place = h("button", { class: "primary wide", text: "Place order" });

  const renderLines = () => {
    if (!state.cart.lines.length) {
      dialog.close();
      return;
    }
    mount(list, ...state.cart.lines.map((line) => cartLine(line, renderLines)));
  };

  place.addEventListener("click", () => void placeOrder(place, message, () => dialog.close()));
  const dialog = sheet("Your order",
    h("div", { class: "sheet-body" },
      list,
      h("label", { class: "field" }, h("span", { text: "Note for the whole order (optional)" }), notes),
      h("p", { class: "fine", text: "Your order goes straight to the kitchen. Please pay at the counter as usual." }),
      message,
    ),
    h("div", { class: "sheet-foot" }, place),
  );
  renderLines();
}

function cartLine(line: CartLine, rerender: () => void): HTMLElement {
  const change = (quantity: number) => {
    state.cart = setQuantity(state.cart, line.key, quantity);
    saveCart();
    rerender();
  };
  return h("li", { class: "cart-line" },
    h("div", { class: "cart-text" },
      h("strong", { text: line.name }),
      ...line.modifierLabels.map((label) => h("span", { class: "mod", text: label })),
      line.notes ? h("span", { class: "note", text: `“${line.notes}”` }) : null,
    ),
    h("div", { class: "stepper small" },
      h("button", { attrs: { "aria-label": `One less ${line.name}` }, text: line.quantity === 1 ? "🗑" : "−", on: { click: () => change(line.quantity - 1) } }),
      h("output", { text: String(line.quantity) }),
      h("button", { attrs: { "aria-label": `One more ${line.name}` }, text: "+", on: { click: () => change(line.quantity + 1) } }),
    ),
  );
}

async function placeOrder(button: HTMLButtonElement, message: HTMLElement, close: () => void): Promise<void> {
  if (state.placing || !state.cart.lines.length) return;
  state.placing = true;
  button.disabled = true;
  button.textContent = "Sending…";
  message.textContent = "";

  // Reuse the key while the cart is unchanged: a retry after a dropped connection can never
  // create a second order. A changed cart gets a fresh key.
  const fingerprint = cartFingerprint(state.cart);
  let pending = storage.get(pendingKey());
  let key = pending?.startsWith(`${fingerprint}\n`) ? pending.slice(fingerprint.length + 1) : null;
  if (!key) {
    key = newIdempotencyKey();
    pending = `${fingerprint}\n${key}`;
    storage.set(pendingKey(), pending);
  }

  try {
    await api.placeOrder(toOrderRequest(state.cart), key);
    state.cart = emptyCart();
    saveCart();
    storage.remove(pendingKey());
    close();
    toast("Order sent to the kitchen 🎉");
    await showOrders();
  } catch (error) {
    if (sessionEnded(error)) {
      close();
      return;
    }
    if (error instanceof ApiError && error.isNetwork) {
      message.textContent = "No connection — your order is not confirmed yet. Tap “Place order” again; it will only be sent once.";
    } else if (error instanceof ApiError && error.status === 409 && error.menuItemId) {
      message.textContent = `${error.message} Please remove it and try again.`;
      state.menuEtag = null;
      void loadMenu().then(renderMenu).catch(() => undefined);
    } else {
      message.textContent = errorText(error);
    }
  } finally {
    state.placing = false;
    button.disabled = false;
    button.textContent = "Place order";
  }
}

// --- orders --------------------------------------------------------------------------------------

async function loadOrders(): Promise<boolean> {
  const result = await api.orders(state.ordersEtag);
  if (!result.data) return false;
  state.orders = result.data;
  state.ordersEtag = result.etag;
  return true;
}

let orderList: HTMLElement | null = null;

async function showOrders(): Promise<void> {
  state.view = "orders";
  try {
    state.ordersEtag = null;
    await loadOrders();
  } catch (error) {
    if (!sessionEnded(error)) toast(errorText(error));
    if (state.view !== "orders") return;
  }
  orderList = h("ul", { class: "orders" });
  mount(root,
    topBar(),
    h("main", { class: "orders-view" },
      h("h1", { text: "My orders" }),
      orderList,
      h("button", { class: "primary wide", text: "Order more", on: { click: () => void showMenu() } }),
      h("button", { class: "link", text: "Leave this table", on: { click: () => void leaveTable() } }),
    ),
    toastRegion,
  );
  renderOrderList();
  startPolling();
}

function renderOrderList(): void {
  if (!orderList) return;
  if (!state.orders.length) {
    mount(orderList, h("li", { class: "empty", text: "You haven't ordered anything yet." }));
    return;
  }
  mount(orderList, ...state.orders.map(orderCard));
}

function orderCard(order: Order): HTMLElement {
  const info = STATUS[order.status];
  let action: Child = null;
  if (order.status === "placed") {
    action = h("button", { class: "secondary", text: "Cancel order", on: { click: () => void cancel(order, "Cancel this order?") } });
  } else if (order.status === "accepted" && !order.cancel_requested) {
    action = h("button", { class: "secondary", text: "Ask to cancel", on: { click: () => void cancel(order, "Ask the staff to cancel this order?") } });
  }
  return h("li", { class: `order tone-${info.tone}` },
    h("div", { class: "order-head" },
      h("strong", { text: `Order #${order.order_number}` }),
      h("span", { class: "time", text: formatTime(order.created_at) }),
    ),
    h("p", { class: `status tone-${info.tone}` },
      h("span", { attrs: { "aria-hidden": "true" }, text: info.icon }),
      h("span", { text: order.cancel_requested && order.status !== "cancelled" ? "Cancellation requested" : info.label }),
    ),
    h("p", { class: "hint", text: order.cancel_requested && order.status !== "cancelled" ? "A member of staff will confirm." : info.hint }),
    h("ul", { class: "order-lines" },
      ...order.items.map((line) =>
        h("li", {},
          h("span", { class: "qty", text: `${line.quantity} ×` }),
          h("span", {},
            h("span", { text: line.name }),
            ...line.modifiers.map((m) => h("span", { class: "mod", text: m })),
            line.notes ? h("span", { class: "note", text: `“${line.notes}”` }) : null,
          ),
        ),
      ),
    ),
    order.notes ? h("p", { class: "note", text: `“${order.notes}”` }) : null,
    action,
  );
}

async function cancel(order: Order, question: string): Promise<void> {
  if (!window.confirm(question)) return;
  try {
    const updated = await api.cancelOrder(order.id);
    state.orders = state.orders.map((o) => (o.id === updated.id ? updated : o));
    state.ordersEtag = null;
    renderOrderList();
    toast(updated.status === "cancelled" ? "Order cancelled" : "Cancellation requested");
  } catch (error) {
    if (!sessionEnded(error)) toast(errorText(error));
    void refresh();
  }
}

async function leaveTable(): Promise<void> {
  if (!window.confirm("Leave this table? You'll need the PIN to order again.")) return;
  try {
    await api.leave();
  } catch {
    /* the session may already be over */
  }
  stopPolling();
  state.cart = emptyCart();
  saveCart();
  showPin("You have left the table. Thank you!");
}
