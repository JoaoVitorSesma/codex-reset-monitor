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
