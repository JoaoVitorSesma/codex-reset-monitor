# Cloudflare scheduler / watchdog

This folder contains the optional V3 external clock. The monitor itself still runs in GitHub Actions; the Worker only:

1. checks whether the GitHub workflow has run recently;
2. sends a Discord watchdog alert if the latest run is stale;
3. triggers `monitor.yml` through `workflow_dispatch`.

The GitHub `schedule` remains enabled as a fallback, but is staggered five minutes after the Cloudflare trigger and skips the monitor when it sees a recent healthy `workflow_dispatch`.

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

The cron in `wrangler.toml` runs at `:07, :22, :37, :52` every hour. The GitHub fallback probe runs at `:12, :27, :42, :57`; when it finds a healthy external dispatch from the previous 10 minutes, it exits without running the monitor a second time.

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

Then wait for the next `:07/:22/:37/:52` UTC trigger and confirm that GitHub Actions shows a `workflow_dispatch` run for **Codex Reset Monitor**. The GitHub fallback probe runs five minutes later at `:12/:27/:42/:57`; when the Cloudflare-triggered run is healthy, the fallback workflow should stop at the gate instead of repeating the monitor.

## Controlled watchdog test

V3.1 includes a protected manual endpoint for testing the direct Cloudflare → Discord watchdog path without creating a fake production incident or changing monitor state.

The endpoint is disabled unless the Cloudflare secret `WATCHDOG_TEST_TOKEN` exists.

From PowerShell, generate a strong one-time token:

```powershell
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$WATCHDOG_TEST_TOKEN = ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
```

Store the same token in Cloudflare:

```powershell
npx wrangler secret put WATCHDOG_TEST_TOKEN
```

Paste the value shown by:

```powershell
$WATCHDOG_TEST_TOKEN
```

Then deploy the current Worker:

```powershell
npx wrangler deploy
```

Run the controlled test:

```powershell
$headers = @{ Authorization = "Bearer $WATCHDOG_TEST_TOKEN" }
Invoke-RestMethod `
  -Method Post `
  -Uri "https://codex-reset-scheduler.codex-reset-monitor-jv.workers.dev/watchdog-test" `
  -Headers $headers
```

Expected HTTP result:

```json
{
  "ok": true,
  "test": "watchdog_direct_discord",
  "state_changed": false
}
```

Discord should receive a blue message titled:

`🧪 CODEX WATCHDOG — TESTE CONTROLADO`

This test only verifies the direct Cloudflare → Discord emergency notification path. It does not mark any reset event, alter `state/alerts.json`, or simulate a real outage.

The Worker also has automated tests for:
- fresh GitHub run → no watchdog alert, but dispatch still occurs;
- stale GitHub run → watchdog alert + dispatch attempt;
- GitHub status lookup failure → direct Discord alert + dispatch attempt;
- workflow dispatch failure → direct Discord watchdog alert.

## Controlled GitHub fallback test

If disabling Cron Triggers is inconvenient or does not propagate immediately, the Worker can intentionally skip its GitHub dispatch while keeping the Cloudflare cron itself active.

Set a temporary Cloudflare secret:

```powershell
npx wrangler secret put FAILOVER_TEST_MODE
```

When prompted, enter exactly:

```text
skip_dispatch
```

Deploy:

```powershell
npx wrangler deploy
```

During the next Cloudflare cron tick, the Worker will still run but will not call GitHub `workflow_dispatch`. The GitHub native schedule, five minutes later, should therefore pass the fallback gate and execute the monitor.

Expected GitHub result:

```text
event = schedule
gate      success
monitor   success
```

As soon as that fallback run is observed, remove the temporary flag:

```powershell
npx wrangler secret delete FAILOVER_TEST_MODE
```

The next Cloudflare cron tick will resume normal `workflow_dispatch` operation. The flag does not change monitor state, does not send a fake Discord incident, and does not modify the configured Cron Trigger.
