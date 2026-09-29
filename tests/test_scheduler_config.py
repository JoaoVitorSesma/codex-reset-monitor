from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
WRANGLER = (ROOT / "cloudflare" / "wrangler.toml").read_text(encoding="utf-8")


def test_primary_and_fallback_crons_are_staggered():
    assert 'crons = ["7,22,37,52 * * * *"]' in WRANGLER
    assert 'cron: "12,27,42,57 * * * *"' in WORKFLOW
    assert 'cron: "7,22,37,52 * * * *"' not in WORKFLOW


def test_schedule_uses_recent_dispatch_gate():
    assert "actions: read" in WORKFLOW
    assert "FALLBACK_RECENT_SECONDS: \"600\"" in WORKFLOW
    assert "event=workflow_dispatch&per_page=1" in WORKFLOW
    assert "needs: gate" in WORKFLOW
    assert "needs.gate.outputs.should_run == 'true'" in WORKFLOW


def test_failed_recent_dispatch_does_not_suppress_fallback():
    assert 'if [[ "$status" != "completed" || "$conclusion" == "success" ]]' in WORKFLOW
    assert "Recent workflow_dispatch failed" in WORKFLOW



def test_fallback_gate_fails_open_if_github_lookup_breaks():
    assert "if ! latest=" in WORKFLOW
    assert "failing open and running the GitHub fallback" in WORKFLOW
    assert '"repos/$REPOSITORY/actions/workflows/monitor.yml/runs?event=workflow_dispatch&per_page=1"' in WORKFLOW


def test_state_push_retries_use_increasing_backoff():
    assert "for attempt in 1 2 3 4; do" in WORKFLOW
    assert "1) retry_delay=5 ;;" in WORKFLOW
    assert "2) retry_delay=15 ;;" in WORKFLOW
    assert "3) retry_delay=30 ;;" in WORKFLOW
    assert 'sleep "$retry_delay"' in WORKFLOW
    assert "after 4 push attempts with backoff" in WORKFLOW


def test_state_push_refresh_failure_is_retryable_but_rebase_conflict_is_fatal():
    assert "Could not refresh origin/main before retry; next push will still be attempted." in WORKFLOW
    assert "Could not rebase monitor state onto latest main." in WORKFLOW
