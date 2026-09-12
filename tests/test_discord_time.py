import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import monitor
import run_monitor


def make_signal(
    text: str,
    source_kind: str,
    created_at: str,
    signal_id: str = "time-test",
) -> monitor.Signal:
    return monitor.Signal(
        id=signal_id,
        source_kind=source_kind,
        source_name="test-source",
        title="Codex reset signal",
        text=text,
        raw=text,
        username="user",
        created_at=created_at,
        url="https://example.com/source",
    )


def field_map(payload: dict) -> dict[str, str]:
    return {
        field["name"]: field["value"]
        for field in payload["embeds"][0]["fields"]
    }


def test_confirmed_tracker_alert_shows_reset_time_and_detection_time():
    signal = make_signal(
        "confirmed forced reset. Global Codex quota reset. Reset has propagated to everyone.",
        source_kind="tracker",
        created_at="2026-09-12T09:00:00+00:00",
    )
    assessment = monitor.Assessment(
        type="automatic_reset",
        status="confirmed",
        scope="global",
        confidence=100,
        temporal="completed",
        certainty="explicit",
    )
    event = {
        "id": "RESET-20260912-TEST",
        "status": "confirmed",
        "scope": "global",
        "confidence": 100,
        "first_seen": "2026-09-12T09:14:00+00:00",
        "sources": [],
    }

    payload = run_monitor.discord_event_payload_with_time(event, signal, assessment)
    fields = field_map(payload)

    assert fields["Horário do reset"] == "12/09/2026 06:00 BRT"
    assert fields["Detectado pelo monitor"] == "12/09/2026 06:14 BRT"


def test_likely_alert_shows_announced_future_time():
    signal = make_signal(
        "Reset incoming for all paid Codex users, landing around 6pm PST today.",
        source_kind="tracker_tibo",
        created_at="2026-09-08T16:00:00+00:00",
    )
    assessment = monitor.assess_signal(signal)
    event = {
        "id": "RESET-20260908-TEST",
        "status": "likely",
        "scope": "global_paid",
        "confidence": assessment.confidence,
        "first_seen": "2026-09-08T16:05:00+00:00",
        "expected_at": assessment.expected_at,
        "sources": [],
    }

    payload = run_monitor.discord_event_payload_with_time(event, signal, assessment)
    fields = field_map(payload)

    assert fields["Horário previsto do reset"] == "08/09/2026 22:00 BRT"
    assert fields["Detectado pelo monitor"] == "08/09/2026 13:05 BRT"
    assert "Horário (Brasil)" not in fields


def test_confirmed_alert_never_invents_missing_reset_time():
    signal = make_signal(
        "Usage limits have been reset for everyone.",
        source_kind="community",
        created_at="2026-09-12T09:10:00+00:00",
    )
    assessment = monitor.Assessment(
        type="automatic_reset",
        status="confirmed",
        scope="global",
        confidence=95,
        temporal="completed",
        certainty="explicit",
    )
    event = {
        "id": "RESET-20260912-NO-TIME",
        "status": "confirmed",
        "scope": "global",
        "confidence": 95,
        "first_seen": "2026-09-12T09:14:00+00:00",
        "sources": [],
    }

    payload = run_monitor.discord_event_payload_with_time(event, signal, assessment)
    fields = field_map(payload)

    assert fields["Horário do reset"] == "Não informado pela fonte"
    assert fields["Detectado pelo monitor"] == "12/09/2026 06:14 BRT"
