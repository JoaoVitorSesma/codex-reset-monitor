# Cloudflare scheduler / watchdog

This folder contains the optional V3 external clock. The monitor itself still runs in GitHub Actions; the Worker only:

1. checks whether the GitHub workflow has run recently;
2. sends a Discord watchdog alert if the latest run is stale;
3. triggers `monitor.yml` through `workflow_dispatch`.

The GitHub `schedule` remains enabled as a fallback.

## Required secrets

Create a fine-grained GitHub token restricted to this repository with **Actions: Read and write**. Store it in Cloudflare as `GITHUB_TOKEN`.

Store the existing Discord webhook in Cloudflare as `DISCORD_WEBHOOK_URL`. Do not commit either value.

## Deploy

From this directory:

```bash
npx wrangler login
npx wrangler secret put GITHUB_TOKEN
npx wrangler secret put DISCORD_WEBHOOK_URL
npx wrangler deploy
```

The cron in `wrangler.toml` runs at `:07, :22, :37, :52` every hour.

## Optional KV deduplication

The Worker works without KV. Without KV, watchdog alerts are limited to the first 15-minute stale window to avoid spam.

For stronger watchdog deduplication, bind a KV namespace as `WATCHDOG_KV`. The Worker will then remember the workflow-run ID that already generated an alert for six hours.

## Manual health endpoint

Opening the deployed Worker URL returns only non-secret health information about the latest GitHub workflow run. It never returns the GitHub token or Discord webhook.


## Windows / PowerShell quick activation

From a local clone of the repository:

```powershell
cd cloudflare
npx wrangler login --use-keyring
npx wrangler deploy
npx wrangler secret put GITHUB_TOKEN
npx wrangler secret put DISCORD_WEBHOOK_URL
```

For `GITHUB_TOKEN`, use a fine-grained GitHub personal access token restricted to `JoaoVitorSesma/codex-reset-monitor` with repository permission **Actions: Read and write**.

For `DISCORD_WEBHOOK_URL`, paste the existing webhook URL directly into Wrangler's secret prompt. Do not commit either secret.

After both secrets are stored, run:

```powershell
npx wrangler deploy
```

Then wait for the next `:07/:22/:37/:52` UTC trigger and confirm that GitHub Actions shows a `workflow_dispatch` run for **Codex Reset Monitor**. The original GitHub `schedule` remains enabled as fallback.
