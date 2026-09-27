import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import monitor


def signal(
    text,
    source_kind="tracker_tibo",
    created_at="2026-09-08T16:00:00Z",
    signal_id="s1",
    username=None,
    title="Codex reset signal",
    raw=None,
):
    return monitor.Signal(
        id=signal_id,
        source_kind=source_kind,
        source_name="test",
        title=title,
        text=text,
        raw=text if raw is None else raw,
        username=(
            username
            if username is not None
            else ("thsottiaux" if source_kind == "tracker_tibo" else "user")
        ),
        created_at=created_at,
        url="https://example.com/source",
    )


def test_completed_global_reset_is_green():
    assessment = monitor.assess_signal(
        signal("Usage limits have been reset for all paid ChatGPT Work and Codex users.")
    )
    assert assessment.type == "automatic_reset"
    assert assessment.status == "confirmed"
    assert assessment.scope == "global_paid"
    assert assessment.confidence >= 95


def test_future_global_reset_is_yellow_and_converts_pt_to_brt():
    assessment = monitor.assess_signal(
        signal("Reset incoming for all paid Codex users, landing around 6pm PST today.")
    )
    assert assessment.type == "automatic_reset"
    assert assessment.status == "likely"
    assert assessment.expected_at == "2026-09-09T01:00:00+00:00"
    assert monitor.display_brt(assessment.expected_at) == "08/09/2026 22:00 BRT"


def test_banked_reset_never_becomes_automatic():
    assessment = monitor.assess_signal(
        signal("All paid Codex users will have a full banked reset available to redeem.")
    )
    assert assessment.type == "banked_reset"
    assert assessment.status == "banked"


def test_user_question_is_ignored():
    assessment = monitor.assess_signal(
        signal("Did anyone get their Codex reset? I think mine is missing.", source_kind="community")
    )
    assert assessment.status is None
    assert assessment.type is None


def test_no_schedule_reply_is_ignored():
    assessment = monitor.assess_signal(signal("There is no schedule, only resets."))
    assert assessment.status is None


def test_yellow_and_green_are_merged_into_same_event():
    state = {"events": []}

    future_signal = signal(
        "Reset incoming for all paid Codex users, landing around 6pm PST today.",
        signal_id="future",
    )
    future_assessment = monitor.assess_signal(future_signal)
    event1, created1, _ = monitor.merge_signal_into_event(
        state, future_signal, future_assessment
    )

    confirmed_signal = signal(
        "Usage limits have been reset for all paid ChatGPT Work and Codex users.",
        created_at="2026-09-09T01:10:00Z",
        signal_id="confirmed",
    )
    confirmed_assessment = monitor.assess_signal(confirmed_signal)
    event2, created2, upgraded2 = monitor.merge_signal_into_event(
        state, confirmed_signal, confirmed_assessment
    )

    assert created1 is True
    assert created2 is False
    assert event1["id"] == event2["id"]
    assert event2["status"] == "confirmed"
    assert upgraded2 is True
    assert len(event2["sources"]) == 2


def test_tracker_history_parser_accepts_current_ledger_shape():
    text = (
        "1. forced reset Global Codex quota reset September 8, 2026 at 2:00 AM UTC "
        "The author explicitly states that usage has now been reset for all paid "
        "subscriptions for ChatGPT Work and Codex. Scope: all paid subscriptions "
        "2. banked reset 7M milestone banked reset July 13, 2026 at 6:29 PM UTC "
        "One manual reset credit was granted to paid Codex users."
    )
    signals = monitor.parse_tracker_history(text)
    assert signals
    first = signals[0]
    assert first.source_kind == "tracker"
    assessment = monitor.assess_signal(first)
    assert assessment.status == "confirmed"


def test_health_alerts_after_three_consecutive_failures_and_recovers():
    state = {}
    for i in range(2):
        should_alert, recovered = monitor.record_source_health(
            state, "source", False, f"error {i}"
        )
        assert should_alert is False
        assert recovered is False

    should_alert, _ = monitor.record_source_health(state, "source", False, "error 3")
    assert should_alert is True

    should_alert_again, _ = monitor.record_source_health(state, "source", False, "error 4")
    assert should_alert_again is False

    _, recovered = monitor.record_source_health(state, "source", True)
    assert recovered is True
    assert state["health"]["source"]["consecutive_failures"] == 0
    assert state["health"]["source"]["last_success"] is not None


def test_success_refreshes_last_success_every_run(monkeypatch):
    times = iter([
        "2026-09-26T10:00:00+00:00",
        "2026-09-26T10:15:00+00:00",
    ])
    monkeypatch.setattr(monitor, "iso_now", lambda: next(times))
    state = {}

    monitor.record_source_health(state, "source", True)
    first = state["health"]["source"]["last_success"]
    monitor.record_source_health(state, "source", True)
    second = state["health"]["source"]["last_success"]

    assert first == "2026-09-26T10:00:00+00:00"
    assert second == "2026-09-26T10:15:00+00:00"


def test_missed_sep26_curly_apostrophe_is_detected_as_future_reset():
    assessment = monitor.assess_signal(
        signal(
            "o yes… we’re back in action and we’ll reset usage limits for all paid users "
            "across Codex and ChatGPT Work sorry about the brief disruption!"
        )
    )
    assert assessment.type == "automatic_reset"
    assert assessment.status == "likely"
    assert assessment.scope == "global_paid"
    assert assessment.temporal == "future"


def test_missed_sep26_straight_apostrophe_is_detected_as_future_reset():
    assessment = monitor.assess_signal(
        signal(
            "We're back in action and we'll reset usage limits for all paid users "
            "across Codex and ChatGPT Work."
        )
    )
    assert assessment.type == "automatic_reset"
    assert assessment.status == "likely"


def test_help_center_example_does_not_create_banked_reset_alert():
    s = signal(
        "For example, if your weekly usage was due to reset on Friday and you use a "
        "full banked reset, your next weekly reset will be around the following Tuesday.",
        source_kind="help",
    )
    assessment = monitor.assess_signal(s)
    assert assessment.type is None
    assert assessment.status is None


def test_real_banked_announcement_still_alerts():
    assessment = monitor.assess_signal(
        signal(
            "We are loading a banked reset into all accounts of our Plus, Pro and Business users. "
            "It will be available to redeem today."
        )
    )
    assert assessment.type == "banked_reset"
    assert assessment.status == "banked"


def test_loose_codexreset_parser_survives_heading_markup_changes():
    text = (
        "Codex reset history 1. forced reset Some heading wrapper Global Codex quota reset "
        "September 26, 2026 at 12:07 AM UTC "
        "The author says we will reset usage limits for all paid users across Codex and ChatGPT Work. "
        "Scope: all paid users"
    )
    signals = monitor.parse_tracker_history(text)
    assert signals
    assert signals[0].source_kind == "tracker"


def test_secondary_tracker_parses_latest_announcement():
    text = (
        "Will Codex Reset? Codex reset announcements "
        "Sep 26, 2026, 12:07 AM UTC "
        "o yes… we’re back in action and we’ll reset usage limits for all paid users "
        "across Codex and ChatGPT Work sorry about the brief disruption! "
        "View announcement "
        "Sep 22, 2026, 6:23 PM UTC banked another event"
    )
    signals = monitor.parse_willcodexresets(text)
    assert len(signals) == 1
    assessment = monitor.assess_signal(signals[0])
    assert assessment.type == "automatic_reset"
    assert assessment.status == "likely"
    assert assessment.scope == "global_paid"



def test_support_guidance_about_banked_reset_is_ignored():
    assessment = monitor.assess_signal(
        signal(
            "Hey, thanks for following up. You can also review our Help Center article "
            "on how banked Codex resets work, including eligibility and troubleshooting "
            "for missing resets. If you're still not able to see the banked reset, "
            "please contact Support.",
            source_kind="community",
            username="OpenAI_Support",
            title="Banked Codex reset guidance",
        )
    )
    assert assessment.type is None
    assert assessment.status is None
    assert assessment.certainty in {"non-announcement", "weak"}


def test_support_explanation_of_available_banked_reset_is_ignored():
    assessment = monitor.assess_signal(
        signal(
            "Banked Codex resets are intended to be account-level rather than desktop-app-only. "
            "Eligible users should be able to view and redeem an available reset from Settings → Usage. "
            "If the issue still persists today, confirm you're using the correct account/workspace "
            "and refresh the Usage page.",
            source_kind="community",
            username="OpenAI_Support",
            title="How to redeem a banked reset",
        )
    )
    assert assessment.type is None
    assert assessment.status is None


def test_bug_report_about_reset_time_is_ignored():
    assessment = monitor.assess_signal(
        signal(
            "I filed the same issue as a GitHub bug report. Codex / Work analytics shows "
            "incorrect 5-hour reset time based on local OS time. The same 5-hour Codex / Work "
            "usage limit shows different reset times depending on which screen is viewed. "
            "What issue are you seeing?",
            source_kind="community",
            username="reqstudio24",
            title="Codex Work analytics reset time bug",
        )
    )
    assert assessment.type is None
    assert assessment.status is None
    assert assessment.scope == "unknown"


def test_usage_limit_complaint_is_ignored_even_if_it_mentions_tibo_elsewhere():
    raw = (
        "There are problems with the decreased Codex usage limits. Here is a failure report "
        "describing CODEX TOKEN ABUSE. Codex wasted an entire week's worth of Plus tokens. "
        "For context someone linked x.com/thsottiaux elsewhere in the thread."
    )
    assessment = monitor.assess_signal(
        signal(
            raw,
            source_kind="community",
            username="pnom",
            title="Usage limits complaint",
            raw=raw,
        )
    )
    assert monitor.source_trust(
        signal(
            raw,
            source_kind="community",
            username="pnom",
            title="Usage limits complaint",
            raw=raw,
        )
    ) == monitor.SOURCE_TRUST["community"]
    assert assessment.type is None
    assert assessment.status is None


def test_tibo_repost_with_direct_reset_language_still_gets_trusted_and_alerts():
    text = (
        "x.com/thsottiaux Tibo @thsottiaux: we're back in action and we'll reset "
        "usage limits for all paid users across Codex and ChatGPT Work."
    )
    s = signal(
        text,
        source_kind="community",
        username="VeitB",
        title="Tibo reset announcement repost",
        raw=text,
    )
    assert monitor.source_trust(s) == monitor.SOURCE_TRUST["community_official"]
    assessment = monitor.assess_signal(s)
    assert assessment.type == "automatic_reset"
    assert assessment.status == "likely"
    assert assessment.scope == "global_paid"


def test_official_support_direct_reset_announcement_still_alerts():
    s = signal(
        "We will reset usage limits for all paid users across Codex and ChatGPT Work tonight.",
        source_kind="community",
        username="OpenAI_Support",
        title="Codex reset announcement",
    )
    assessment = monitor.assess_signal(s)
    assert assessment.type == "automatic_reset"
    assert assessment.status == "likely"
    assert assessment.scope == "global_paid"



def test_load_state_prunes_known_false_positive_events(tmp_path, monkeypatch):
    state_path = tmp_path / "alerts.json"
    payload = {
        "version": 2,
        "initialized": True,
        "seen_signal_fingerprints": [],
        "source_fingerprints": {},
        "health": {},
        "events": [
            {
                "id": "RESET-20260927-25183CE0",
                "type": "automatic_reset",
                "status": "likely",
            },
            {
                "id": "RESET-20990101-VALID",
                "type": "automatic_reset",
                "status": "confirmed",
            },
        ],
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(monitor, "STATE_PATH", state_path)

    loaded = monitor.load_state()
    ids = {event["id"] for event in loaded["events"]}

    assert "RESET-20260927-25183CE0" not in ids
    assert "RESET-20990101-VALID" in ids



def test_discord_test_payload_reports_current_v3_architecture():
    payload = monitor.discord_test_payload()
    embed = payload["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}

    assert embed["title"] == "🧪 CODEX MONITOR V3 — TESTE"
    assert "V2" not in str(payload)
    assert "Cloudflare Cron" in fields["Execução"]
    assert "workflow_dispatch" in fields["Execução"]
    assert "GitHub schedule" in fields["Execução"]
    assert "OpenAI Help" in fields["Fontes"]
    assert "OpenAI Developer Community" in fields["Fontes"]
    assert "codexreset.org" in fields["Fontes"]
    assert "willcodexresets.com" in fields["Fontes"]
    assert "watchdog externo" in fields["Proteções"]
    assert fields["Custo"] == "Sem X API e sem LLM/API paga."
