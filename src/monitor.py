from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

STATE_PATH = Path("state/alerts.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
TEST_DISCORD = os.environ.get("TEST_DISCORD", "false").lower() == "true"

HELP_URL = "https://help.openai.com/en/articles/20001498-how-banked-codex-resets-work"
COMMUNITY_BASE = "https://community.openai.com"
CODEXRESET_URL = "https://codexreset.org/"
CODEXRESET_HISTORY_URL = "https://codexreset.org/codex-reset-history"

COMMUNITY_QUERIES = [
    "codex reset order:latest",
    "banked reset codex order:latest",
    "global reset codex order:latest",
    '"reset incoming" codex order:latest',
]

HEADERS = {
    "User-Agent": "codex-reset-monitor/2.0 (+https://github.com/JoaoVitorSesma/codex-reset-monitor)",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}

BRT = ZoneInfo("America/Sao_Paulo")
PACIFIC = ZoneInfo("America/Los_Angeles")

TRUSTED_USERNAMES = {
    "OpenAI_Support",
    "logankilpatrick",
    "sama",
}

SOURCE_TRUST = {
    "help": 100,
    "community_official": 92,
    "community": 35,
    "tracker": 85,
    "tracker_tibo": 95,
}

BANKED_PATTERNS = [
    r"\bbanked reset\b",
    r"\bsaved reset\b",
    r"\bfull banked reset\b",
    r"\breset credit\b",
    r"\bredeem(?:able)? reset\b",
]

COMPLETED_PATTERNS = [
    r"\b(?:have|has|we'?ve|i'?ve) reset\b",
    r"\busage limits? (?:have|has) been reset\b",
    r"\breset (?:has been )?propagated\b",
    r"\bit is done\b",
    r"\bbrand new usage\b",
    r"\breset(?:ting)? usage (?:now|for all|for everyone)\b",
    r"\bglobal (?:codex )?(?:quota )?reset\b",
    r"\bhard reset\b",
]

FUTURE_PATTERNS = [
    r"\breset incoming\b",
    r"\bwill (?:do|land|have|receive|reset)\b",
    r"\bwill be reset\b",
    r"\breset .* within (?:the next )?(?:hour|few hours|minutes)\b",
    r"\blanding\b",
    r"\breset .* (?:today|tonight|tomorrow)\b",
    r"\blittle surprise .* tomorrow\b",
]

GLOBAL_SCOPE_PATTERNS = [
    r"\bglobal\b",
    r"\bshared\b",
    r"\ball paid\b",
    r"\ball .*codex.*users\b",
    r"\beveryone\b",
    r"\bacross codex\b",
]

NEGATIVE_PATTERNS = [
    r"\bi think\b",
    r"\bdoes anyone\b",
    r"\banyone else\b",
    r"\bi wish\b",
    r"\bmy reset\b",
    r"\bmissing reset\b",
    r"\bdid not receive\b",
    r"\bdid anyone\b",
    r"\bno schedule\b",
]

RESET_ACTION_PATTERNS = [
    r"\breset\b",
    r"\bbrand new usage\b",
    r"\brestore(?:d|s|ing)? usage\b",
    r"\breplenish(?:ed|ing)?\b",
]

HEALTH_FAILURE_THRESHOLD = 8
MAX_EVENTS = 250
MAX_SOURCE_FINGERPRINTS = 1000


@dataclass
class Signal:
    id: str
    source_kind: str
    source_name: str
    title: str
    text: str
    url: str
    created_at: str
    raw: str = ""
    username: str = ""


@dataclass
class Assessment:
    type: str | None
    status: str | None
    scope: str
    confidence: int
    temporal: str
    certainty: str
    expected_at: str | None = None
    reasons: list[str] = field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def clean_text(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "html.parser")
    text = html.unescape(soup.get_text(" ", strip=True))
    return re.sub(r"\s+", " ", text).strip()


def contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = dateparser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_recent(created_at: str, hours: int = 120) -> bool:
    dt = parse_dt(created_at)
    return bool(dt and dt >= utc_now() - timedelta(hours=hours))


def load_state() -> dict:
    default = {
        "version": 2,
        "initialized": False,
        "seen_signal_fingerprints": [],
        "source_fingerprints": {},
        "events": [],
        "health": {},
    }
    if not STATE_PATH.exists():
        return default

    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default

    if raw.get("version") != 2:
        return {
            **default,
            # V2 adds new sources and event clustering, so it deliberately creates
            # a fresh silent baseline on its first normal run.
            "initialized": False,
            "seen_signal_fingerprints": list(raw.get("seen_alerts", [])),
            "source_fingerprints": dict(raw.get("source_fingerprints", {})),
        }

    for key, value in default.items():
        raw.setdefault(key, value)
    return raw


def save_state(state: dict) -> None:
    state["version"] = 2
    state["events"] = state.get("events", [])[-MAX_EVENTS:]
    state["seen_signal_fingerprints"] = state.get("seen_signal_fingerprints", [])[-1000:]
    source_fp = state.get("source_fingerprints", {})
    if len(source_fp) > MAX_SOURCE_FINGERPRINTS:
        source_fp = dict(list(source_fp.items())[-MAX_SOURCE_FINGERPRINTS:])
    state["source_fingerprints"] = source_fp
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def trusted_community_context(signal: Signal) -> bool:
    lower = f"{signal.username} {signal.text} {signal.raw}".lower()
    if signal.username in TRUSTED_USERNAMES:
        return True
    if "openai support" in lower:
        return True
    if "x.com/thsottiaux" in lower or "twitter.com/thsottiaux" in lower:
        return True
    if "@thsottiaux" in lower or re.search(r"\btibo\b", lower):
        return True
    return False


def source_trust(signal: Signal) -> int:
    if signal.source_kind == "community" and trusted_community_context(signal):
        return SOURCE_TRUST["community_official"]
    return SOURCE_TRUST.get(signal.source_kind, 20)


def extract_scope(text: str) -> str:
    lower = text.lower()
    if contains_any(lower, GLOBAL_SCOPE_PATTERNS):
        if "paid" in lower:
            return "global_paid"
        return "global"
    if "codex" in lower and ("chatgpt work" in lower or "work" in lower):
        return "shared_codex_work"
    return "unknown"


def extract_expected_at(text: str, created_at: str) -> str | None:
    base = parse_dt(created_at) or utc_now()
    lower = text.lower()

    m = re.search(r"\bwithin (?:the next )?(\d+)\s*(minute|minutes|hour|hours)\b", lower)
    if m:
        amount = int(m.group(1))
        delta = timedelta(minutes=amount) if "minute" in m.group(2) else timedelta(hours=amount)
        return (base + delta).isoformat()

    if re.search(r"\bwithin (?:the next )?(?:hour)\b", lower):
        return (base + timedelta(hours=1)).isoformat()

    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(pt|pst|pdt)\b", lower)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0

        local_base = base.astimezone(PACIFIC)
        day = local_base.date()
        if "tomorrow" in lower:
            day = day + timedelta(days=1)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=PACIFIC)

        if candidate.astimezone(timezone.utc) < base - timedelta(hours=2) and "yesterday" not in lower:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc).isoformat()

    return None


def assess_signal(signal: Signal) -> Assessment:
    text = f"{signal.title} {signal.text}".strip()
    lower = text.lower()
    trust = source_trust(signal)
    scope = extract_scope(lower)
    banked = contains_any(lower, BANKED_PATTERNS)
    completed = contains_any(lower, COMPLETED_PATTERNS)
    future = contains_any(lower, FUTURE_PATTERNS)
    global_scope = scope != "unknown"
    negative = contains_any(lower, NEGATIVE_PATTERNS)
    reset_action = contains_any(lower, RESET_ACTION_PATTERNS)

    temporal = "completed" if completed else "future" if future else "ambiguous"
    certainty = "explicit" if (completed or future or banked) else "weak"
    expected_at = extract_expected_at(text, signal.created_at)

    reasons = [f"source_trust={trust}"]
    confidence = trust

    if banked:
        reasons.append("banked-language")
        confidence += 5
        if trust >= 70:
            return Assessment(
                type="banked_reset",
                status="banked",
                scope=scope,
                confidence=min(confidence, 100),
                temporal=temporal,
                certainty="explicit",
                expected_at=expected_at,
                reasons=reasons,
            )

    if global_scope:
        confidence += 8
        reasons.append(f"scope={scope}")

    if completed:
        confidence += 8
        reasons.append("completed-language")

    if future:
        confidence += 5
        reasons.append("future-language")

    if expected_at:
        confidence += 4
        reasons.append("concrete-time")

    if negative:
        confidence -= 25
        reasons.append("negative/speculative-language")

    if not reset_action:
        confidence -= 30
        reasons.append("no-reset-action")

    confidence = max(0, min(confidence, 100))
    tracker_confirmed = signal.source_kind == "tracker" and "confirmed" in lower

    if completed and global_scope and trust >= 80 and confidence >= 90:
        return Assessment(
            type="automatic_reset",
            status="confirmed",
            scope=scope,
            confidence=confidence,
            temporal="completed",
            certainty="explicit",
            expected_at=expected_at,
            reasons=reasons,
        )

    if tracker_confirmed and global_scope and confidence >= 85:
        return Assessment(
            type="automatic_reset",
            status="confirmed",
            scope=scope,
            confidence=max(confidence, 92),
            temporal="completed",
            certainty="explicit",
            expected_at=expected_at,
            reasons=reasons + ["tracker-ledger-confirmed"],
        )

    if future and global_scope and trust >= 75 and confidence >= 70:
        return Assessment(
            type="automatic_reset",
            status="likely",
            scope=scope,
            confidence=confidence,
            temporal="future",
            certainty="explicit",
            expected_at=expected_at,
            reasons=reasons,
        )

    return Assessment(
        type=None,
        status=None,
        scope=scope,
        confidence=confidence,
        temporal=temporal,
        certainty=certainty,
        expected_at=expected_at,
        reasons=reasons,
    )


def display_brt(value: str | None) -> str:
    dt = parse_dt(value)
    if not dt:
        return "Não informado"
    return dt.astimezone(BRT).strftime("%d/%m/%Y %H:%M BRT")


def time_remaining(value: str | None) -> str | None:
    dt = parse_dt(value)
    if not dt:
        return None
    seconds = int((dt - utc_now()).total_seconds())
    if seconds <= 0:
        return None
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"~{hours}h{minutes:02d}"
    return f"~{minutes} min"


def normalized_source(signal: Signal, assessment: Assessment) -> dict:
    return {
        "signal_id": signal.id,
        "source_kind": signal.source_kind,
        "source_name": signal.source_name,
        "url": signal.url,
        "created_at": signal.created_at,
        "confidence": assessment.confidence,
        "evidence": signal.text[:700],
    }


def event_anchor(signal: Signal, assessment: Assessment) -> datetime:
    return parse_dt(assessment.expected_at) or parse_dt(signal.created_at) or utc_now()


def compatible_scope(a: str, b: str) -> bool:
    globalish = {"global", "global_paid", "shared_codex_work"}
    return a == b or (a in globalish and b in globalish)


def find_matching_event(state: dict, signal: Signal, assessment: Assessment) -> dict | None:
    anchor = event_anchor(signal, assessment)
    for event in reversed(state.get("events", [])):
        if event.get("type") != assessment.type:
            continue
        if not compatible_scope(event.get("scope", "unknown"), assessment.scope):
            continue

        event_anchor_value = event.get("expected_at") or event.get("last_seen") or event.get("first_seen")
        event_dt = parse_dt(event_anchor_value)
        if not event_dt:
            continue

        if abs((anchor - event_dt).total_seconds()) <= 18 * 3600:
            return event
    return None


def new_event_id(signal: Signal, assessment: Assessment) -> str:
    anchor = event_anchor(signal, assessment)
    prefix = "BANKED" if assessment.type == "banked_reset" else "RESET"
    digest = sha(f"{assessment.type}|{assessment.scope}|{anchor.date().isoformat()}")[:8].upper()
    return f"{prefix}-{anchor.strftime('%Y%m%d')}-{digest}"


def merge_signal_into_event(state: dict, signal: Signal, assessment: Assessment) -> tuple[dict, bool, bool]:
    event = find_matching_event(state, signal, assessment)
    created = event is None

    if event is None:
        event = {
            "id": new_event_id(signal, assessment),
            "type": assessment.type,
            "status": assessment.status,
            "scope": assessment.scope,
            "confidence": assessment.confidence,
            "first_seen": iso_now(),
            "last_seen": iso_now(),
            "expected_at": assessment.expected_at,
            "sources": [],
            "notified_statuses": [],
        }
        state.setdefault("events", []).append(event)

    old_status = event.get("status")
    event["last_seen"] = iso_now()
    event["confidence"] = max(int(event.get("confidence", 0)), assessment.confidence)
    if not event.get("expected_at") and assessment.expected_at:
        event["expected_at"] = assessment.expected_at

    source = normalized_source(signal, assessment)
    existing_source_ids = {s.get("signal_id") for s in event.get("sources", [])}
    if source["signal_id"] not in existing_source_ids:
        event.setdefault("sources", []).append(source)

    if event["type"] == "automatic_reset":
        rank = {"likely": 1, "confirmed": 2}
        if rank.get(assessment.status or "", 0) > rank.get(old_status or "", 0):
            event["status"] = assessment.status
    elif event["type"] == "banked_reset":
        event["status"] = "banked"

    upgraded = old_status == "likely" and event.get("status") == "confirmed"
    return event, created, upgraded


def webhook_base(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def send_discord(payload: dict) -> dict | None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL secret is missing")
    response = requests.post(
        webhook_base(DISCORD_WEBHOOK_URL),
        params={"wait": "true"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    try:
        return response.json()
    except Exception:
        return None


def discord_event_payload(event: dict, signal: Signal, assessment: Assessment, upgraded: bool = False) -> dict:
    status = event["status"]
    if status == "confirmed":
        heading = "🟢 RESET CODEX AUTOMÁTICO CONFIRMADO"
        color = 0x2ECC71
        recommendation = "Reset global/automático confirmado. Nenhuma ação manual deve ser necessária; valide o saldo em Settings → Usage."
    elif status == "likely":
        heading = "🟡 RESET CODEX AUTOMÁTICO MUITO PROVÁVEL"
        color = 0xF1C40F
        recommendation = "Sinal forte de reset futuro. Se ainda houver quota útil, considere antecipar uma sessão antes do horário indicado."
    else:
        heading = "🟣 BANKED RESET CODEX — AÇÃO MANUAL"
        color = 0x9B59B6
        recommendation = "Banked reset não restaura a quota automaticamente. Resgate manualmente em Settings → Usage quando estiver disponível."

    if upgraded:
        heading = "🟢 CONFIRMADO — atualização do alerta anterior"

    fields = [
        {"name": "Evento", "value": event["id"], "inline": True},
        {"name": "Confiança", "value": f"{event['confidence']}%", "inline": True},
        {"name": "Escopo", "value": event.get("scope", "unknown"), "inline": True},
    ]

    if event.get("expected_at"):
        fields.append({"name": "Horário (Brasil)", "value": display_brt(event["expected_at"]), "inline": True})
        remaining = time_remaining(event["expected_at"])
        if remaining:
            fields.append({"name": "Tempo restante", "value": remaining, "inline": True})

    fields.extend([
        {"name": "Fonte primária desta atualização", "value": f"{signal.source_name}\n{signal.url}"[:1000], "inline": False},
        {"name": "Evidência", "value": signal.text[:900] or "(sem texto)", "inline": False},
        {"name": "Ação recomendada", "value": recommendation, "inline": False},
    ])

    if len(event.get("sources", [])) > 1:
        names = []
        for source in event["sources"][-5:]:
            name = source.get("source_name", source.get("source_kind", "fonte"))
            if name not in names:
                names.append(name)
        fields.append({"name": "Corroboração", "value": " · ".join(names)[:1000], "inline": False})

    return {
        "username": "Codex Reset Monitor",
        "embeds": [{
            "title": heading,
            "description": "Atualização vinculada ao mesmo evento." if upgraded else "Novo sinal relevante detectado pelo monitor.",
            "color": color,
            "fields": fields,
            "footer": {"text": "Classificação determinística; sem API paga ou LLM externo."},
            "timestamp": iso_now(),
        }],
    }


def discord_health_payload(source_name: str, health: dict, recovered: bool = False) -> dict:
    if recovered:
        heading = "✅ CODEX MONITOR — FONTE RECUPERADA"
        color = 0x2ECC71
        description = f"A fonte **{source_name}** voltou a responder normalmente."
    else:
        heading = "⚠️ CODEX MONITOR DEGRADADO"
        color = 0xE67E22
        description = (
            f"A fonte **{source_name}** falhou em {health.get('consecutive_failures', 0)} "
            "execuções consecutivas. O monitor continua com cobertura parcial."
        )
    return {
        "username": "Codex Reset Monitor",
        "embeds": [{
            "title": heading,
            "description": description,
            "color": color,
            "fields": [
                {"name": "Último sucesso", "value": health.get("last_success") or "Ainda não registrado", "inline": False},
                {"name": "Último erro", "value": (health.get("last_error") or "—")[:900], "inline": False},
            ],
            "timestamp": iso_now(),
        }],
    }


def record_source_health(state: dict, source_name: str, ok: bool, error: str | None = None) -> tuple[bool, bool]:
    health = state.setdefault("health", {}).setdefault(source_name, {
        "consecutive_failures": 0,
        "last_success": None,
        "last_failure": None,
        "last_error": None,
        "degraded_alert_sent": False,
    })

    was_degraded = bool(health.get("degraded_alert_sent"))
    if ok:
        had_failures = int(health.get("consecutive_failures", 0)) > 0
        health["consecutive_failures"] = 0
        if health.get("last_success") is None or had_failures:
            health["last_success"] = iso_now()
        health["last_error"] = None
        health["degraded_alert_sent"] = False
        return False, was_degraded

    health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
    health["last_failure"] = iso_now()
    health["last_error"] = error or "unknown error"
    should_alert = (
        health["consecutive_failures"] >= HEALTH_FAILURE_THRESHOLD
        and not health.get("degraded_alert_sent")
    )
    if should_alert:
        health["degraded_alert_sent"] = True
    return should_alert, False


def fetch_help() -> list[Signal]:
    response = requests.get(HELP_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    text = clean_text(response.text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    relevant = [
        sentence.strip()
        for sentence in sentences
        if ("reset" in sentence.lower() or "usage limit" in sentence.lower())
        and len(sentence.strip()) >= 25
    ]
    return [
        Signal(
            id=f"help-{sha(sentence.lower())[:20]}",
            source_kind="help",
            source_name="OpenAI Help Center",
            title="OpenAI Help Center — Codex reset update",
            text=sentence,
            raw=sentence,
            username="OpenAI Help Center",
            created_at=iso_now(),
            url=HELP_URL,
        )
        for sentence in relevant
    ]


def fetch_community() -> list[Signal]:
    collected: dict[str, Signal] = {}
    successes = 0
    errors: list[str] = []

    for query in COMMUNITY_QUERIES:
        try:
            response = requests.get(
                f"{COMMUNITY_BASE}/search.json",
                params={"q": query},
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            successes += 1
        except Exception as exc:
            errors.append(f"{query!r}: {exc}")
            continue

        topics = {str(t.get("id")): t for t in data.get("topics", [])}
        for post in data.get("posts", []):
            created_at = post.get("created_at", "")
            if not is_recent(created_at, hours=120):
                continue

            post_id = str(post.get("id"))
            topic_id = str(post.get("topic_id"))
            username = post.get("username", "")
            topic = topics.get(topic_id, {})
            title = topic.get("title") or f"OpenAI Developer Community post {post_id}"
            raw = post.get("blurb", "")
            text = clean_text(raw)

            try:
                full_response = requests.get(
                    f"{COMMUNITY_BASE}/posts/{post_id}.json",
                    headers=HEADERS,
                    timeout=20,
                )
                if full_response.ok:
                    full = full_response.json()
                    raw = full.get("cooked", raw)
                    text = clean_text(raw)
                    username = full.get("username", username)
            except Exception:
                pass

            combined = f"{title} {text}".lower()
            if "codex" not in combined or "reset" not in combined:
                continue

            signal = Signal(
                id=f"community-{post_id}",
                source_kind="community",
                source_name=f"OpenAI Developer Community / {username or 'user'}",
                title=title,
                text=text,
                raw=raw,
                username=username,
                created_at=created_at,
                url=f"{COMMUNITY_BASE}/t/{topic_id}/{post.get('post_number', 1)}",
            )
            collected[post_id] = signal

    if successes == 0:
        raise RuntimeError("all Community queries failed: " + " | ".join(errors))
    return list(collected.values())


def parse_tracker_history(text: str) -> list[Signal]:
    normalized = re.sub(r"\s+", " ", text)
    entry_pattern = re.compile(
        r"(?P<kind>forced reset|banked reset|compensation)\s+"
        r"(?P<title>(?:Global Codex[^.]{0,100}reset|[^.]{0,100}banked reset|[^.]{0,100}hard reset|[^.]{0,100}reset))\s+"
        r"(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M UTC)\s+"
        r"(?P<body>.*?)(?=(?:forced reset|banked reset|compensation)\s+|\Z)",
        flags=re.I,
    )

    signals: list[Signal] = []
    for match in list(entry_pattern.finditer(normalized))[:12]:
        created = parse_dt(match.group("date"))
        if not created or created < utc_now() - timedelta(days=14):
            continue
        kind = match.group("kind").lower()
        title = clean_text(match.group("title"))
        body = clean_text(match.group("body"))[:1800]
        status_word = "confirmed" if kind in {"forced reset", "compensation"} else "banked"
        signal_text = f"{status_word} {kind}. {title}. {body}"
        signals.append(Signal(
            id=f"tracker-{sha(match.group(0))[:20]}",
            source_kind="tracker",
            source_name="codexreset.org",
            title=title,
            text=signal_text,
            raw=match.group(0),
            username="codexreset.org",
            created_at=created.isoformat(),
            url=CODEXRESET_HISTORY_URL,
        ))
    return signals


def parse_tracker_tibo(text: str) -> list[Signal]:
    normalized = re.sub(r"\s+", " ", text)
    matches = re.finditer(r"Tibo Sottiaux\s*@thsottiaux(?P<body>.{0,1400})", normalized, flags=re.I)
    signals: list[Signal] = []
    for match in matches:
        body = match.group("body")
        body = re.split(
            r"(?:OpenAI\s*@OpenAI|Romain Huet\s*@|Greg Brockman\s*@|Sam Altman\s*@|Codex reset timeline)",
            body,
            maxsplit=1,
            flags=re.I,
        )[0]
        body = clean_text(body)
        if "reset" not in body.lower():
            continue
        signals.append(Signal(
            id=f"tracker-tibo-{sha(body)[:20]}",
            source_kind="tracker_tibo",
            source_name="Tibo via codexreset.org",
            title="Latest monitored Tibo signal",
            text=body,
            raw=body,
            username="thsottiaux",
            created_at=iso_now(),
            url=CODEXRESET_URL,
        ))
    return signals[:3]


def fetch_codexreset() -> list[Signal]:
    history_response = requests.get(CODEXRESET_HISTORY_URL, headers=HEADERS, timeout=30)
    history_response.raise_for_status()
    history_text = BeautifulSoup(history_response.text, "html.parser").get_text(" ", strip=True)

    home_response = requests.get(CODEXRESET_URL, headers=HEADERS, timeout=30)
    home_response.raise_for_status()
    home_text = BeautifulSoup(home_response.text, "html.parser").get_text(" ", strip=True)

    signals = parse_tracker_history(history_text)
    signals.extend(parse_tracker_tibo(home_text))
    if not signals:
        raise RuntimeError("tracker responded but no supported reset/Tibo signals were parsed")
    return signals


SOURCE_FETCHERS = {
    "openai_help": fetch_help,
    "openai_community": fetch_community,
    "codexreset": fetch_codexreset,
}


def signal_fingerprint(signal: Signal, assessment: Assessment) -> str:
    normalized = re.sub(r"\W+", " ", signal.text.lower())[:1400]
    return sha(f"{assessment.type}|{assessment.status}|{signal.source_kind}|{signal.url}|{normalized}")


def should_notify(event: dict) -> bool:
    return event.get("status") not in set(event.get("notified_statuses", []))


def mark_notified(event: dict) -> None:
    statuses = event.setdefault("notified_statuses", [])
    if event.get("status") not in statuses:
        statuses.append(event["status"])


def main() -> int:
    state = load_state()

    if TEST_DISCORD:
        send_discord({
            "username": "Codex Reset Monitor",
            "embeds": [{
                "title": "🧪 CODEX MONITOR V2 — TESTE",
                "description": "Webhook, GitHub Actions e formato de embeds V2 estão funcionando.",
                "color": 0x3498DB,
                "fields": [
                    {"name": "Arquitetura", "value": "OpenAI Help + Community + codexreset.org + event engine + health monitoring", "inline": False},
                    {"name": "Custo", "value": "Sem X API e sem LLM/API paga.", "inline": False},
                ],
                "timestamp": iso_now(),
            }],
        })
        print("Discord V2 test sent successfully")
        return 0

    all_signals: list[Signal] = []
    health_notifications: list[tuple[str, bool]] = []

    for source_name, fetcher in SOURCE_FETCHERS.items():
        try:
            signals = fetcher()
            all_signals.extend(signals)
            _, recovered = record_source_health(state, source_name, True)
            if recovered:
                health_notifications.append((source_name, True))
            print(f"{source_name}: {len(signals)} signal(s)")
        except Exception as exc:
            should_alert, _ = record_source_health(state, source_name, False, str(exc))
            print(f"{source_name} failed: {exc}", file=sys.stderr)
            if should_alert:
                health_notifications.append((source_name, False))

    seen = set(state.get("seen_signal_fingerprints", []))
    fingerprints = state.setdefault("source_fingerprints", {})
    pending_notifications: list[tuple[dict, Signal, Assessment, bool]] = []

    for signal in all_signals:
        assessment = assess_signal(signal)
        fingerprints[signal.id] = sha(signal.text)
        if not assessment.status or not assessment.type:
            continue

        fp = signal_fingerprint(signal, assessment)
        if fp in seen:
            continue
        seen.add(fp)

        event, _, upgraded = merge_signal_into_event(state, signal, assessment)

        if not state.get("initialized", False):
            continue

        if should_notify(event):
            pending_notifications.append((event, signal, assessment, upgraded))

    if not state.get("initialized", False):
        state["initialized"] = True
        state["seen_signal_fingerprints"] = list(seen)
        save_state(state)
        print(f"V2 baseline initialized with {len(all_signals)} signal(s) and {len(state['events'])} event(s); no alerts sent")
        return 0

    for source_name, recovered in health_notifications:
        health = state["health"][source_name]
        send_discord(discord_health_payload(source_name, health, recovered=recovered))

    for event, signal, assessment, upgraded in pending_notifications:
        send_discord(discord_event_payload(event, signal, assessment, upgraded=upgraded))
        mark_notified(event)
        print(f"Sent {event['status']} alert for {event['id']} from {signal.source_name}")

    state["seen_signal_fingerprints"] = list(seen)
    save_state(state)
    print(
        f"Checked {len(all_signals)} signal(s); "
        f"events={len(state.get('events', []))}; "
        f"notifications={len(pending_notifications)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
