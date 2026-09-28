const DEFAULTS = {
  repository: "JoaoVitorSesma/codex-reset-monitor",
  workflow: "monitor.yml",
  ref: "main",
  staleMinutes: 40,
};

function githubHeaders(env) {
  return {
    "Accept": "application/vnd.github+json",
    "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
    "X-GitHub-Api-Version": "2026-03-10",
    "User-Agent": "codex-reset-monitor-cloudflare-scheduler/1.1",
  };
}

export async function latestRun(env, fetchImpl = fetch) {
  const repository = env.GITHUB_REPOSITORY || DEFAULTS.repository;
  const workflow = env.GITHUB_WORKFLOW || DEFAULTS.workflow;
  const url =
    `https://api.github.com/repos/${repository}/actions/workflows/${workflow}/runs?per_page=1`;
  const response = await fetchImpl(url, { headers: githubHeaders(env) });
  if (!response.ok) {
    throw new Error(`GitHub runs lookup failed: ${response.status} ${await response.text()}`);
  }
  const data = await response.json();
  return data.workflow_runs?.[0] || null;
}

export async function dispatchMonitor(env, fetchImpl = fetch) {
  const repository = env.GITHUB_REPOSITORY || DEFAULTS.repository;
  const workflow = env.GITHUB_WORKFLOW || DEFAULTS.workflow;
  const ref = env.GITHUB_REF || DEFAULTS.ref;
  const url =
    `https://api.github.com/repos/${repository}/actions/workflows/${workflow}/dispatches`;

  const response = await fetchImpl(url, {
    method: "POST",
    headers: {
      ...githubHeaders(env),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref,
      inputs: {
        send_test: false,
      },
    }),
  });

  if (!response.ok) {
    throw new Error(`GitHub workflow dispatch failed: ${response.status} ${await response.text()}`);
  }
}

export async function sendDiscord(env, payload, fetchImpl = fetch) {
  if (!env.DISCORD_WEBHOOK_URL) return;
  const response = await fetchImpl(env.DISCORD_WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Discord watchdog alert failed: ${response.status} ${await response.text()}`);
  }
}

function watchdogAlertPayload({ title, description, fields = [], color = 0xE74C3C }) {
  return {
    username: "Codex Reset Monitor",
    embeds: [{
      title,
      description,
      color,
      fields,
      timestamp: new Date().toISOString(),
    }],
  };
}

export function controlledWatchdogTestPayload() {
  return watchdogAlertPayload({
    title: "🧪 CODEX WATCHDOG — TESTE CONTROLADO",
    description:
      "Teste manual do caminho direto Cloudflare → Discord. " +
      "Nenhuma falha real foi detectada e nenhum estado do monitor foi alterado.",
    color: 0x3498DB,
    fields: [
      {
        name: "Objetivo",
        value: "Validar que o watchdog externo consegue avisar o Discord mesmo sem depender do GitHub Actions.",
        inline: false,
      },
    ],
  });
}

export async function maybeAlertStaleRun(
  env,
  run,
  { nowMs = Date.now(), fetchImpl = fetch } = {},
) {
  if (!run?.created_at) return false;

  const staleMinutes = Number(env.WATCHDOG_STALE_MINUTES || DEFAULTS.staleMinutes);
  const ageMinutes = (nowMs - Date.parse(run.created_at)) / 60000;
  if (ageMinutes < staleMinutes) return false;

  const alertKey = `stale-run:${run.id}`;
  if (env.WATCHDOG_KV) {
    const alreadySent = await env.WATCHDOG_KV.get(alertKey);
    if (alreadySent) return false;
    await env.WATCHDOG_KV.put(alertKey, "1", { expirationTtl: 21600 });
  } else {
    // Without KV, alert only during the first 15-minute stale window so the
    // watchdog does not spam Discord on every cron tick.
    if (ageMinutes >= staleMinutes + 15) return false;
  }

  await sendDiscord(env, watchdogAlertPayload({
    title: "🔴 CODEX MONITOR — EXECUÇÃO ATRASADA",
    description:
      "O relógio externo detectou que o workflow principal não executa dentro da janela esperada. " +
      "O Cloudflare continuará tentando dispará-lo.",
    fields: [
      {
        name: "Última execução observada",
        value: run.created_at,
        inline: false,
      },
      {
        name: "Atraso aproximado",
        value: `~${Math.floor(ageMinutes)} min`,
        inline: true,
      },
      {
        name: "Último resultado",
        value: run.conclusion || run.status || "desconhecido",
        inline: true,
      },
    ],
  }), fetchImpl);
  return true;
}

export async function runScheduledCycle(
  env,
  { fetchImpl = fetch, nowMs = Date.now() } = {},
) {
  let lookupFailed = false;

  try {
    const run = await latestRun(env, fetchImpl);
    await maybeAlertStaleRun(env, run, { nowMs, fetchImpl });
  } catch (error) {
    lookupFailed = true;
    await sendDiscord(env, watchdogAlertPayload({
      title: "🔴 CODEX WATCHDOG — GITHUB INDISPONÍVEL",
      description:
        "O Cloudflare não conseguiu consultar o estado recente do GitHub Actions. " +
        "O disparo do workflow ainda será tentado.",
      fields: [{
        name: "Erro",
        value: String(error?.message || error).slice(0, 900),
        inline: false,
      }],
    }), fetchImpl);
  }

  if (env.FAILOVER_TEST_MODE === "skip_dispatch") {
    console.log("Controlled failover test mode: skipping this Cloudflare workflow_dispatch.");
    return { skipped_dispatch: true };
  }

  try {
    await dispatchMonitor(env, fetchImpl);
  } catch (error) {
    if (!lookupFailed) {
      await sendDiscord(env, watchdogAlertPayload({
        title: "🔴 CODEX WATCHDOG — FALHA AO DISPARAR WORKFLOW",
        description:
          "O Cloudflare tentou iniciar o monitor no GitHub Actions, mas o workflow_dispatch falhou.",
        fields: [{
          name: "Erro",
          value: String(error?.message || error).slice(0, 900),
          inline: false,
        }],
      }), fetchImpl);
    }
    throw error;
  }

  return { skipped_dispatch: false };
}

function bearerToken(request) {
  const value = request.headers.get("Authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(runScheduledCycle(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/watchdog-test") {
      if (request.method !== "POST") {
        return Response.json(
          { ok: false, error: "method_not_allowed" },
          { status: 405, headers: { Allow: "POST" } },
        );
      }

      if (!env.WATCHDOG_TEST_TOKEN) {
        return Response.json(
          { ok: false, error: "watchdog_test_disabled" },
          { status: 404 },
        );
      }

      if (bearerToken(request) !== env.WATCHDOG_TEST_TOKEN) {
        return Response.json(
          { ok: false, error: "unauthorized" },
          { status: 401 },
        );
      }

      try {
        await sendDiscord(env, controlledWatchdogTestPayload());
        return Response.json({
          ok: true,
          test: "watchdog_direct_discord",
          state_changed: false,
        });
      } catch (error) {
        return Response.json(
          { ok: false, error: String(error?.message || error) },
          { status: 500 },
        );
      }
    }

    // Lightweight manual health endpoint. It never exposes secrets.
    try {
      const run = await latestRun(env);
      return Response.json({
        ok: true,
        scheduler: "cloudflare",
        repository: env.GITHUB_REPOSITORY || DEFAULTS.repository,
        latest_run: run
          ? {
              id: run.id,
              status: run.status,
              conclusion: run.conclusion,
              created_at: run.created_at,
            }
          : null,
      });
    } catch (error) {
      return Response.json(
        { ok: false, error: String(error?.message || error) },
        { status: 500 },
      );
    }
  },
};
