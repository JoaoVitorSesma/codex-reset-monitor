import assert from "node:assert/strict";
import test from "node:test";

import worker, {
  controlledWatchdogTestPayload,
  maybeAlertStaleRun,
  runScheduledCycle,
} from "./worker.js";

function jsonResponse(body, init = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status || 200,
    headers: { "Content-Type": "application/json" },
  });
}

function makeEnv(overrides = {}) {
  return {
    GITHUB_TOKEN: "test-github-token",
    DISCORD_WEBHOOK_URL: "https://discord.example/webhook",
    GITHUB_REPOSITORY: "JoaoVitorSesma/codex-reset-monitor",
    GITHUB_WORKFLOW: "monitor.yml",
    GITHUB_REF: "main",
    WATCHDOG_STALE_MINUTES: "40",
    ...overrides,
  };
}

test("fresh run dispatches GitHub without Discord watchdog alert", async () => {
  const nowMs = Date.parse("2026-09-28T12:00:00Z");
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET", body: options.body });
    if (String(url).includes("/runs?per_page=1")) {
      return jsonResponse({
        workflow_runs: [{
          id: 1,
          created_at: "2026-09-28T11:50:00Z",
          status: "completed",
          conclusion: "success",
        }],
      });
    }
    if (String(url).includes("/dispatches")) {
      return new Response(null, { status: 204 });
    }
    if (String(url).startsWith("https://discord.example/")) {
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  await runScheduledCycle(makeEnv(), { fetchImpl, nowMs });

  assert.equal(calls.filter((c) => c.url.startsWith("https://discord.example/")).length, 0);
  assert.equal(calls.filter((c) => c.url.includes("/dispatches")).length, 1);
});

test("stale run alerts Discord and still dispatches GitHub", async () => {
  const nowMs = Date.parse("2026-09-28T12:00:00Z");
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET", body: options.body });
    if (String(url).includes("/runs?per_page=1")) {
      return jsonResponse({
        workflow_runs: [{
          id: 2,
          created_at: "2026-09-28T11:15:00Z",
          status: "completed",
          conclusion: "success",
        }],
      });
    }
    if (String(url).includes("/dispatches")) {
      return new Response(null, { status: 204 });
    }
    if (String(url).startsWith("https://discord.example/")) {
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  await runScheduledCycle(makeEnv(), { fetchImpl, nowMs });

  const discordCalls = calls.filter((c) => c.url.startsWith("https://discord.example/"));
  assert.equal(discordCalls.length, 1);
  assert.match(discordCalls[0].body, /EXECUÇÃO ATRASADA/);
  assert.equal(calls.filter((c) => c.url.includes("/dispatches")).length, 1);
});

test("GitHub lookup failure alerts Discord and still attempts dispatch", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET", body: options.body });
    if (String(url).includes("/runs?per_page=1")) {
      return new Response("upstream unavailable", { status: 503 });
    }
    if (String(url).includes("/dispatches")) {
      return new Response(null, { status: 204 });
    }
    if (String(url).startsWith("https://discord.example/")) {
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  await runScheduledCycle(makeEnv(), { fetchImpl, nowMs: Date.now() });

  const discordCalls = calls.filter((c) => c.url.startsWith("https://discord.example/"));
  assert.equal(discordCalls.length, 1);
  assert.match(discordCalls[0].body, /GITHUB INDISPONÍVEL/);
  assert.equal(calls.filter((c) => c.url.includes("/dispatches")).length, 1);
});

test("dispatch failure alerts Discord when lookup was healthy", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET", body: options.body });
    if (String(url).includes("/runs?per_page=1")) {
      return jsonResponse({
        workflow_runs: [{
          id: 3,
          created_at: new Date().toISOString(),
          status: "completed",
          conclusion: "success",
        }],
      });
    }
    if (String(url).includes("/dispatches")) {
      return new Response("forbidden", { status: 403 });
    }
    if (String(url).startsWith("https://discord.example/")) {
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  await assert.rejects(
    () => runScheduledCycle(makeEnv(), { fetchImpl, nowMs: Date.now() }),
    /workflow dispatch failed/,
  );

  const discordCalls = calls.filter((c) => c.url.startsWith("https://discord.example/"));
  assert.equal(discordCalls.length, 1);
  assert.match(discordCalls[0].body, /FALHA AO DISPARAR WORKFLOW/);
});

test("controlled watchdog payload is clearly marked as a test", () => {
  const payload = controlledWatchdogTestPayload();
  assert.match(payload.embeds[0].title, /TESTE CONTROLADO/);
  assert.match(payload.embeds[0].description, /Nenhuma falha real foi detectada/);
});

test("watchdog-test endpoint requires token and sends direct Discord test", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET", body: options.body });
    if (String(url).startsWith("https://discord.example/")) {
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  try {
    const env = makeEnv({ WATCHDOG_TEST_TOKEN: "secret-test-token" });

    const unauthorized = await worker.fetch(
      new Request("https://worker.example/watchdog-test", { method: "POST" }),
      env,
    );
    assert.equal(unauthorized.status, 401);

    const authorized = await worker.fetch(
      new Request("https://worker.example/watchdog-test", {
        method: "POST",
        headers: { Authorization: "Bearer secret-test-token" },
      }),
      env,
    );
    assert.equal(authorized.status, 200);
    const body = await authorized.json();
    assert.equal(body.ok, true);
    assert.equal(body.state_changed, false);

    const discordCalls = calls.filter((c) => c.url.startsWith("https://discord.example/"));
    assert.equal(discordCalls.length, 1);
    assert.match(discordCalls[0].body, /CODEX WATCHDOG/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("stale helper returns false outside the stale window", async () => {
  const sent = await maybeAlertStaleRun(
    makeEnv(),
    {
      id: 10,
      created_at: "2026-09-28T11:50:00Z",
      status: "completed",
      conclusion: "success",
    },
    {
      nowMs: Date.parse("2026-09-28T12:00:00Z"),
      fetchImpl: async () => {
        throw new Error("Discord must not be called");
      },
    },
  );
  assert.equal(sent, false);
});


test("controlled failover mode skips Cloudflare dispatch without sending a false alert", async () => {
  const nowMs = Date.parse("2026-09-28T12:00:00Z");
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET", body: options.body });
    if (String(url).includes("/runs?per_page=1")) {
      return jsonResponse({
        workflow_runs: [{
          id: 20,
          created_at: "2026-09-28T11:50:00Z",
          status: "completed",
          conclusion: "success",
        }],
      });
    }
    if (String(url).startsWith("https://discord.example/")) {
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  const result = await runScheduledCycle(
    makeEnv({ FAILOVER_TEST_MODE: "skip_dispatch" }),
    { fetchImpl, nowMs },
  );

  assert.equal(result.skipped_dispatch, true);
  assert.equal(calls.filter((c) => c.url.includes("/dispatches")).length, 0);
  assert.equal(calls.filter((c) => c.url.startsWith("https://discord.example/")).length, 0);
});
