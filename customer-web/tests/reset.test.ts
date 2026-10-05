import assert from "node:assert/strict";
import { test } from "node:test";

import { passwordProblem, readResetToken } from "../src/reset.ts";

const TOKEN = "a".repeat(43);

test("the reset token is read from the URL fragment, and only if well-formed", () => {
  assert.equal(readResetToken(`#token=${TOKEN}`), TOKEN);
  assert.equal(readResetToken(`token=${TOKEN}`), TOKEN);
  assert.equal(readResetToken(""), null);
  assert.equal(readResetToken("#token=short"), null);
  assert.equal(readResetToken(`#token=${TOKEN}%3Cscript%3E`), null);
});

test("new passwords are checked before they are sent", () => {
  assert.equal(passwordProblem("short", "short"), "Use at least 8 characters.");
  assert.equal(passwordProblem("long-enough-1", "long-enough-2"), "The two passwords are not the same.");
  assert.equal(passwordProblem("x".repeat(129), "x".repeat(129)), "Use at most 128 characters.");
  assert.equal(passwordProblem("long-enough-1", "long-enough-1"), null);
});
