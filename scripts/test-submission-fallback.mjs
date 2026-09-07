import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { canFallbackToLocalStorage } from "../src/lib/submission-fallback.mjs";

// Execute the real API and page handler with only browser/network boundaries
// replaced. Errors must originate in fetchAPI, not in synthetic Error fixtures.
const apiSource = readFileSync(new URL("../src/lib/api.js", import.meta.url), "utf8");
const pageSource = readFileSync(new URL("../src/app/submit-project/page.jsx", import.meta.url), "utf8");
const handlerSource = pageSource.slice(
  pageSource.indexOf("  const handleSubmit ="),
  pageSource.indexOf("  if (isAuthenticated === null)"),
);

for (const { status, count } of [
  { status: 400, count: 0 }, { status: 401, count: 0 },
  { status: 403, count: 0 }, { status: 422, count: 0 },
  { status: 500, count: 1 }, { status: 503, count: 1 },
  { status: null, count: 1 }, { status: 201, count: 0 },
]) {
  for (const jsonBody of status === null || status === 201 ? [true] : [true, false]) {
    test(`submission through API: ${status ?? "network"}, ${jsonBody ? "JSON" : "non-JSON"} response`, async () => {
      const storage = new Map([["eventra_token", "synthetic-token"], ["eventra_user", "{}"]]);
      const state = { error: "", success: "", submitting: false };
      const redirects = [];
      const timers = [];
      const context = vm.createContext({
        process: { env: {} },
        console: { error() {} },
        window: { location: { href: "" } },
        localStorage: {
          getItem: key => storage.get(key) ?? null,
          setItem: (key, value) => storage.set(key, value),
          removeItem: key => storage.delete(key),
        },
        fetch: async (url, options) => {
          assert.equal(url, "http://localhost:8080/api/projects");
          assert.equal(options.method, "POST");
          assert.equal(JSON.parse(options.body).title, "Repair fixture");
          if (status === null) throw new TypeError("Failed to fetch");
          return new Response(jsonBody ? JSON.stringify({ message: "Controlled rejection", id: 123 }) : "Unavailable", {
            status, statusText: "Controlled rejection",
            headers: { "Content-Type": jsonBody ? "application/json" : "text/plain" },
          });
        },
        canFallbackToLocalStorage,
        formData: { title: " Repair fixture ", description: "Synthetic submission", category: "Developer Tools", githubUrl: "", demoUrl: "", thumbnailUrl: "" },
        submitting: false,
        setErrorMessage: value => { state.error = value; },
        setSuccessMessage: value => { state.success = value; },
        setSubmitting: value => { state.submitting = value; },
        router: { push: path => redirects.push(path) },
        setTimeout: callback => timers.push(callback),
        clearTimeout() {},
      });
      vm.runInContext(apiSource.replace(/^export /gm, ""), context);
      const submit = vm.runInContext(`${handlerSource}\nhandleSubmit`, context);
      await submit({ preventDefault() {} });
      assert.equal(JSON.parse(storage.get("eventra_custom_projects") ?? "[]").length, count);
      assert.equal(state.submitting, false);
      if (count === 1) {
        assert.match(state.success, /saved locally/i);
        assert.equal(state.error, "");
      } else if (status === 201) {
        assert.match(state.success, /submitted successfully/i);
        assert.equal(state.error, "");
      } else {
        assert.equal(state.success, "");
        assert.match(state.error, status === 401 ? /Session expired/ : /Controlled rejection/);
        assert.equal(timers.length, 0);
      }
      if (status === 401) {
        assert.equal(storage.has("eventra_token"), false);
        assert.equal(storage.has("eventra_user"), false);
        assert.equal(context.window.location.href, "/login");
      } else {
        assert.equal(storage.has("eventra_token"), true);
        assert.equal(context.window.location.href, "");
      }
      for (const callback of timers) callback();
      assert.deepEqual(redirects, count === 1 || status === 201 ? ["/projects"] : []);
    });
  }
}

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
