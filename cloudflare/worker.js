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
    "User-Agent": "codex-reset-monitor-cloudflare-scheduler/1.0",
  };
}

async function latestRun(env) {
  const repository = env.GITHUB_REPOSITORY || DEFAULTS.repository;
  const workflow = env.GITHUB_WORKFLOW || DEFAULTS.workflow;
  const url =
    `https://api.github.com/repos/${repository}/actions/workflows/${workflow}/runs?per_page=1`;
  const response = await fetch(url, { headers: githubHeaders(env) });
  if (!response.ok) {
    throw new Error(`GitHub runs lookup failed: ${response.status} ${await response.text()}`);
  }
  const data = await response.json();
  return data.workflow_runs?.[0] || null;
}

async function dispatchMonitor(env) {
  const repository = env.GITHUB_REPOSITORY || DEFAULTS.repository;
  const workflow = env.GITHUB_WORKFLOW || DEFAULTS.workflow;
  const ref = env.GITHUB_REF || DEFAULTS.ref;
  const url =
    `https://api.github.com/repos/${repository}/actions/workflows/${workflow}/dispatches`;

  const response = await fetch(url, {
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

async function sendDiscord(env, payload) {
  if (!env.DISCORD_WEBHOOK_URL) return;
  const response = await fetch(env.DISCORD_WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Discord watchdog alert failed: ${response.status} ${await response.text()}`);
  }
}

async function maybeAlertStaleRun(env, run) {
  if (!run?.created_at) return;

  const staleMinutes = Number(env.WATCHDOG_STALE_MINUTES || DEFAULTS.staleMinutes);
  const ageMinutes = (Date.now() - Date.parse(run.created_at)) / 60000;
  if (ageMinutes < staleMinutes) return;

  const alertKey = `stale-run:${run.id}`;
  if (env.WATCHDOG_KV) {
    const alreadySent = await env.WATCHDOG_KV.get(alertKey);
    if (alreadySent) return;
    await env.WATCHDOG_KV.put(alertKey, "1", { expirationTtl: 21600 });
  } else {
    // Without KV, alert only during the first 15-minute stale window so the
    // watchdog does not spam Discord on every cron tick.
    if (ageMinutes >= staleMinutes + 15) return;
  }

  await sendDiscord(env, {
    username: "Codex Reset Monitor",
    embeds: [{
      title: "🔴 CODEX MONITOR — EXECUÇÃO ATRASADA",
      description:
        "O relógio externo detectou que o workflow principal não executa dentro da janela esperada. " +
        "O Cloudflare continuará tentando dispará-lo.",
      color: 0xE74C3C,
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
      timestamp: new Date().toISOString(),
    }],
  });
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil((async () => {
      const run = await latestRun(env);
      await maybeAlertStaleRun(env, run);
      await dispatchMonitor(env);
    })());
  },

  async fetch(_request, env) {
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
