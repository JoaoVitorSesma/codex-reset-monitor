import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import monitor


def signal(text, source_kind="tracker_tibo", created_at="2026-09-08T16:00:00Z", signal_id="s1"):
    return monitor.Signal(
        id=signal_id,
        source_kind=source_kind,
        source_name="test",
        title="Codex reset signal",
        text=text,
        raw=text,
        username="thsottiaux" if source_kind == "tracker_tibo" else "user",
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


def test_health_alerts_only_after_eight_consecutive_failures():
    state = {}
    for i in range(7):
        should_alert, recovered = monitor.record_source_health(
            state, "source", False, f"error {i}"
        )
        assert should_alert is False
        assert recovered is False

    should_alert, _ = monitor.record_source_health(state, "source", False, "error 8")
    assert should_alert is True

    should_alert_again, _ = monitor.record_source_health(state, "source", False, "error 9")
    assert should_alert_again is False

    _, recovered = monitor.record_source_health(state, "source", True)
    assert recovered is True
