from __future__ import annotations

import monitor

_BASE_DISCORD_EVENT_PAYLOAD = monitor.discord_event_payload


def _detected_at(event: dict) -> str:
    return monitor.display_brt(event.get("first_seen"))


def _time_fields(event: dict, signal: monitor.Signal, assessment: monitor.Assessment) -> list[dict]:
    """Build explicit reset-time fields without inventing timestamps.

    Rules:
    - Future/likely events use the parsed announced time when available.
    - Confirmed tracker-history entries use the ledger timestamp as the reset time.
    - A previously announced time on a confirmed event is labeled as announced,
      not silently promoted to an exact occurrence timestamp.
    - When the source gives no usable time, say so explicitly and always show
      when the monitor first detected the event.
    """

    status = event.get("status")
    fields: list[dict] = []

    if status == "likely":
        expected_at = event.get("expected_at") or assessment.expected_at
        fields.append({
            "name": "Horário previsto do reset",
            "value": monitor.display_brt(expected_at) if expected_at else "Não informado pela fonte",
            "inline": True,
        })
        if expected_at:
            remaining = monitor.time_remaining(expected_at)
            if remaining:
                fields.append({"name": "Tempo restante", "value": remaining, "inline": True})

    elif status == "confirmed":
        # codexreset.org history timestamps represent the recorded reset event,
        # rather than merely the time at which this GitHub monitor fetched it.
        if signal.source_kind == "tracker" and signal.created_at:
            fields.append({
                "name": "Horário do reset",
                "value": monitor.display_brt(signal.created_at),
                "inline": True,
            })
        elif assessment.temporal == "completed" and assessment.expected_at:
            fields.append({
                "name": "Horário do reset",
                "value": monitor.display_brt(assessment.expected_at),
                "inline": True,
            })
        elif event.get("expected_at"):
            fields.append({
                "name": "Horário anunciado do reset",
                "value": monitor.display_brt(event["expected_at"]),
                "inline": True,
            })
        else:
            fields.append({
                "name": "Horário do reset",
                "value": "Não informado pela fonte",
                "inline": True,
            })

    else:  # banked reset
        expected_at = event.get("expected_at") or assessment.expected_at
        fields.append({
            "name": "Horário de disponibilidade",
            "value": monitor.display_brt(expected_at) if expected_at else "Não informado pela fonte",
            "inline": True,
        })

    fields.append({
        "name": "Detectado pelo monitor",
        "value": _detected_at(event),
        "inline": True,
    })
    return fields


def discord_event_payload_with_time(
    event: dict,
    signal: monitor.Signal,
    assessment: monitor.Assessment,
    upgraded: bool = False,
) -> dict:
    payload = _BASE_DISCORD_EVENT_PAYLOAD(event, signal, assessment, upgraded)
    embed = payload["embeds"][0]

    # V2 already had a generic expected-time field. Replace it with status-aware
    # wording so a predicted time is never presented as an exact completed reset.
    fields = [
        field
        for field in embed.get("fields", [])
        if field.get("name") not in {"Horário (Brasil)", "Tempo restante"}
    ]

    insertion_index = min(3, len(fields))
    fields[insertion_index:insertion_index] = _time_fields(event, signal, assessment)
    embed["fields"] = fields
    return payload


def main() -> int:
    monitor.discord_event_payload = discord_event_payload_with_time
    return monitor.main()


if __name__ == "__main__":
    raise SystemExit(main())
