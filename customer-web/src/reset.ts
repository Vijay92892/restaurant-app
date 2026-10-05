// Staff password reset, opened from the emailed link: /reset-password#token=…
// The token sits in the URL fragment (never sent to any server, not even in Referer) and is
// removed from the address bar at once, so it doesn't linger in history or get shared by accident.

import { h, mount } from "./dom.ts";

const TOKEN = /^[A-Za-z0-9_-]{20,128}$/;
const MIN_LENGTH = 8;

export function readResetToken(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  return token && TOKEN.test(token) ? token : null;
}

/** Why the new password can't be sent yet, or null when it can. */
export function passwordProblem(password: string, repeat: string): string | null {
  if (password.length < MIN_LENGTH) return `Use at least ${MIN_LENGTH} characters.`;
  if (password.length > 128) return "Use at most 128 characters.";
  if (password !== repeat) return "The two passwords are not the same.";
  return null;
}

export function startReset(root: HTMLElement): void {
  document.title = "Choose a new password";
  const token = readResetToken(location.hash);
  history.replaceState(null, "", location.pathname);
  if (!token) {
    done(root, "This link is not complete. Open the link from the email again, or ask for a new one in the staff app.");
    return;
  }

  const password = h("input", {
    class: "text-input",
    attrs: { id: "new-password", type: "password", autocomplete: "new-password", required: true, maxlength: 128 },
  });
  const repeat = h("input", {
    class: "text-input",
    attrs: { id: "repeat-password", type: "password", autocomplete: "new-password", required: true, maxlength: 128 },
  });
  const error = h("p", { class: "error", attrs: { role: "alert" } });
  const submit = h("button", { class: "primary", attrs: { type: "submit" }, text: "Save new password" });

  const form = h("form", {
    class: "pin-form",
    attrs: { novalidate: true },
    on: {
      submit: (event) => {
        event.preventDefault();
        const problem = passwordProblem(password.value, repeat.value);
        error.textContent = problem ?? "";
        if (problem) return;
        submit.disabled = true;
        submit.textContent = "Saving…";
        void send(token, password.value).then((failure) => {
          if (failure === null) {
            done(root, "Your password is changed. Sign in to the staff app with it. You have been signed out everywhere else.");
            return;
          }
          error.textContent = failure;
          submit.disabled = false;
          submit.textContent = "Save new password";
        });
      },
    },
  },
    h("label", { attrs: { for: "new-password" }, text: "New password" }),
    password,
    h("label", { attrs: { for: "repeat-password" }, text: "Repeat it" }),
    repeat,
    h("p", { class: "help", text: `At least ${MIN_LENGTH} characters. Avoid passwords you use elsewhere.` }),
    error,
    submit,
  );
  mount(root, h("main", { class: "pin-screen" }, h("h1", { text: "Choose a new password" }), h("p", { class: "table", text: "Restaurant staff account" }), form));
  password.focus();
}

/** null on success, otherwise what to tell the person. */
async function send(token: string, newPassword: string): Promise<string | null> {
  let response: Response;
  try {
    response = await fetch("/api/v1/auth/password-reset/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "omit",
      cache: "no-store",
      body: JSON.stringify({ token, new_password: newPassword }),
    });
  } catch {
    return "No internet connection. Please try again.";
  }
  if (response.ok) return null;
  try {
    const body = (await response.json()) as { error?: { message?: unknown } };
    if (typeof body.error?.message === "string") return body.error.message;
  } catch {
    // fall through
  }
  return "This link has expired or was already used. Ask for a new one in the staff app.";
}

function done(root: HTMLElement, text: string): void {
  mount(root, h("main", { class: "message" }, h("p", { text })));
}
