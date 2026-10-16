import assert from "node:assert/strict";
import { test } from "node:test";

import { isPositionError, positionErrorText, refusalText, toReport } from "../src/location.ts";

test("a position's odd timestamps are replaced by the moment it arrived", () => {
  const now = Date.UTC(2026, 9, 17, 12, 0, 0);
  const coords = { latitude: 28.2449519, longitude: 76.8135882, accuracy: 18.5 };
  const iso = (ms: number) => new Date(ms).toISOString();
  assert.equal(toReport({ coords, timestamp: now - 2_000 }, now).captured_at, iso(now - 2_000)); // normal
  assert.equal(toReport({ coords, timestamp: now * 1000 }, now).captured_at, iso(now)); // WebKit: µs
  assert.equal(toReport({ coords, timestamp: now + 86_400_000 }, now).captured_at, iso(now)); // a day off
  assert.equal(toReport({ coords, timestamp: Number.NaN }, now).captured_at, iso(now));
});

test("a browser position becomes the report the server checks", () => {
  const now = Date.UTC(2026, 9, 17, 12, 0, 0);
  const report = toReport({
    coords: { latitude: 28.2449519, longitude: 76.8135882, accuracy: 18.5 },
    timestamp: now,
  }, now);
  assert.deepEqual(report, {
    latitude: 28.2449519,
    longitude: 76.8135882,
    accuracy_m: 18.5,
    captured_at: "2026-10-17T12:00:00.000Z",
  });
  // Only these four facts are ever sent: no boolean like "near the restaurant".
  assert.deepEqual(Object.keys(report).sort(), ["accuracy_m", "captured_at", "latitude", "longitude"]);
});

test("browser failures are explained in plain words", () => {
  assert.equal(positionErrorText(1), "Location permission was not given.");
  assert.equal(positionErrorText(2), "Your phone couldn't find its location.");
  assert.equal(positionErrorText(3), "Finding your location took too long.");
  assert.equal(isPositionError({ code: 1, message: "denied" }), true);
  assert.equal(isPositionError({ code: "location_outside" }), false); // an API error's code
  assert.equal(isPositionError(null), false);
});

test("server refusals never mention a distance", () => {
  for (const code of ["location_outside", "location_inaccurate", "location_stale", "location_reused", "access_method_off"]) {
    const text = refusalText(code, "server text 123");
    assert.ok(text.length > 0);
    assert.equal(/\d/.test(text), false, code);
  }
  assert.equal(refusalText("something_new", "As the server said."), "As the server said.");
});
