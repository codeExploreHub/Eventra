import assert from "node:assert/strict";
import test from "node:test";

import { canFallbackToLocalStorage } from "../src/lib/submission-fallback.mjs";

for (const status of [400, 401, 403, 404, 422, 499]) {
  test(`HTTP ${status} must not allow local submission fallback`, () => {
    const error = Object.assign(new Error("Request rejected"), { status });
    assert.equal(canFallbackToLocalStorage(error), false);
  });
}

for (const status of [500, 502, 503, 599, 600]) {
  test(`HTTP ${status} allows local submission fallback`, () => {
    const error = Object.assign(new Error("Server unavailable"), { status });
    assert.equal(canFallbackToLocalStorage(error), true);
  });
}

test("a network error without a response status allows local fallback", () => {
  assert.equal(canFallbackToLocalStorage(new TypeError("Failed to fetch")), true);
});

test("absent errors and zero status preserve the no-response fallback", () => {
  assert.equal(canFallbackToLocalStorage(undefined), true);
  assert.equal(canFallbackToLocalStorage(null), true);
  assert.equal(canFallbackToLocalStorage({ status: 0 }), true);
});

test("a present status below the server-error threshold does not allow fallback", () => {
  assert.equal(canFallbackToLocalStorage({ status: 200 }), false);
  assert.equal(canFallbackToLocalStorage({ status: 302 }), false);
});
