import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

STATE_PATH = Path("state/alerts.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
TEST_DISCORD = os.environ.get("TEST_DISCORD", "false").lower() == "true"

HELP_URL = "https://help.openai.com/en/articles/20001498-how-banked-codex-resets-work"
COMMUNITY_BASE = "https://community.openai.com"
COMMUNITY_QUERIES = [
    "codex reset order:latest",
    "banked reset codex order:latest",
    "global reset codex order:latest",
    '"reset incoming" codex order:latest',
]

HEADERS = {
    "User-Agent": "codex-reset-monitor/1.0 (+private GitHub Actions monitor)",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}

TRUSTED_USERNAMES = {
    "OpenAI_Support",
    "sam.saffron",
    "logankilpatrick",
    "sama",
}

AUTO_CONFIRMED_PATTERNS = [
    r"global reset",
    r"reset(?:ting)? (?:the )?(?:codex )?usage limits? for (?:all|everyone)",
    r"usage limits? (?:were|are|have been|will be) reset",
    r"hard reset",
    r"all reset for everyone",
]

AUTO_LIKELY_PATTERNS = [
    r"reset incoming",
    r"will reset (?:the )?(?:codex )?usage limits?",
    r"reset button (?:has been |was )?pressed",
    r"reset(?:ting)? .* (?:tonight|today|tomorrow|within the hour)",
    r"landing .*\b(?:am|pm|pt|pst|pdt|utc)\b",
]

BANKED_PATTERNS = [
    r"banked reset",
    r"saved reset",
    r"full banked reset",
    r"reset available",
]

NEGATIVE_PATTERNS = [
    r"i think",
    r"does anyone",
    r"anyone else",
    r"i wish",
    r"i would like",
    r"my reset",
    r"missing reset",
    r"did not receive",
]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"initialized": False, "seen_alerts": [], "source_fingerprints": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_text(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "html.parser")
    text = html.unescape(soup.get_text(" ", strip=True))
    return re.sub(r"\s+", " ", text).strip()


def is_recent(created_at: str, hours: int = 96) -> bool:
    try:
        dt = dateparser.parse(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)
    except Exception:
        return False


def contains_any(text: str, patterns: list[str]) -> bool:
    lower = text.lower()
    return any(re.search(pattern, lower, flags=re.I) for pattern in patterns)


def trusted_context(username: str, text: str, raw: str) -> bool:
    lower = f"{text} {raw}".lower()
    if username in TRUSTED_USERNAMES:
        return True
    if "x.com/thsottiaux" in lower or "twitter.com/thsottiaux" in lower:
        return True
    if "@thsottiaux" in lower or re.search(r"\btibo\b", lower):
        return True
    if "openai support" in lower:
        return True
    return False


def classify(text: str, raw: str, username: str, source_kind: str) -> str | None:
    joined = f"{text} {raw}".lower()

    # Banked reset always wins over generic reset wording unless there is
    # separate, explicit global/hard-reset language.
    banked = contains_any(joined, BANKED_PATTERNS)
    automatic = contains_any(joined, AUTO_CONFIRMED_PATTERNS)
    likely = contains_any(joined, AUTO_LIKELY_PATTERNS)

    if banked and not automatic:
        return "banked"

    if source_kind == "help" and automatic:
        return "confirmed"

    trusted = trusted_context(username, text, raw)
    if trusted and automatic:
        return "confirmed"

    if trusted and likely:
        return "likely"

    return None


def discord_payload(level: str, title: str, body: str, source_url: str) -> dict:
    if level == "confirmed":
        heading = "🟢 RESET CODEX AUTOMÁTICO CONFIRMADO"
        color = 0x2ECC71
    elif level == "likely":
        heading = "🟡 RESET CODEX AUTOMÁTICO MUITO PROVÁVEL"
        color = 0xF1C40F
    elif level == "banked":
        heading = "🟣 BANKED RESET CODEX — AÇÃO MANUAL"
        color = 0x9B59B6
    else:
        heading = "🧪 CODEX MONITOR — TESTE"
        color = 0x3498DB

    recommendation = {
        "confirmed": "Reset automático/global: nenhuma ação manual deve ser necessária. Confira Settings → Usage para validar o saldo.",
        "likely": "Sinal forte, mas ainda não confirmado. Se você tiver muita quota restante e houver horário próximo, considere antecipar uma sessão longa.",
        "banked": "Isto não deve alterar sua quota automaticamente. Abra Settings → Usage e resgate manualmente o reset quando ele aparecer.",
        "test": "Webhook do Discord e GitHub Actions estão funcionando corretamente.",
    }[level]

    description = body[:1500]
    return {
        "username": "Codex Reset Monitor",
        "embeds": [
            {
                "title": heading,
                "description": description,
                "color": color,
                "fields": [
                    {"name": "Fonte", "value": source_url[:1000], "inline": False},
                    {"name": "Recomendação", "value": recommendation, "inline": False},
                ],
                "footer": {"text": title[:200]},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }


def send_discord(payload: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL secret is missing")
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    response.raise_for_status()


def fetch_help() -> list[dict]:
    response = requests.get(HELP_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    text = clean_text(response.text)
    # Keep only reset-relevant neighborhoods to avoid unrelated page changes.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    relevant = [
        s for s in sentences
        if "reset" in s.lower() or "usage limit" in s.lower() or "september" in s.lower()
    ]
    relevant_text = " ".join(relevant)
    if not relevant_text:
        return []
    return [{
        "id": "help-banked-resets",
        "title": "OpenAI Help Center — banked/automatic Codex resets",
        "text": relevant_text,
        "raw": response.text,
        "username": "OpenAI Help Center",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": HELP_URL,
        "source_kind": "help",
    }]


def fetch_community() -> list[dict]:
    collected: dict[str, dict] = {}
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
        except Exception as exc:
            print(f"Community search failed for {query!r}: {exc}", file=sys.stderr)
            continue

        topics = {str(t.get("id")): t for t in data.get("topics", [])}
        for post in data.get("posts", []):
            created_at = post.get("created_at", "")
            if not is_recent(created_at, hours=96):
                continue
            post_id = str(post.get("id"))
            topic_id = str(post.get("topic_id"))
            blurb = clean_text(post.get("blurb", ""))
            username = post.get("username", "")
            topic = topics.get(topic_id, {})
            title = topic.get("title") or f"OpenAI Developer Community post {post_id}"
            raw = post.get("blurb", "")

            # Fetch full post to preserve embedded quoted X/Twitter text when available.
            try:
                p = requests.get(
                    f"{COMMUNITY_BASE}/posts/{post_id}.json",
                    headers=HEADERS,
                    timeout=20,
                )
                if p.ok:
                    full = p.json()
                    raw = full.get("cooked", raw)
                    blurb = clean_text(raw)
                    username = full.get("username", username)
            except Exception:
                pass

            combined = f"{title} {blurb}"
            if not ("codex" in combined.lower() and "reset" in combined.lower()):
                continue
            if contains_any(combined, NEGATIVE_PATTERNS) and not trusted_context(username, blurb, raw):
                continue

            collected[post_id] = {
                "id": f"community-{post_id}",
                "title": title,
                "text": blurb,
                "raw": raw,
                "username": username,
                "created_at": created_at,
                "url": f"{COMMUNITY_BASE}/t/{topic_id}/{post.get('post_number', 1)}",
                "source_kind": "community",
            }
    return list(collected.values())


def event_fingerprint(item: dict, level: str) -> str:
    normalized = re.sub(r"\W+", " ", item["text"].lower())[:1200]
    return sha(f"{level}|{item['url']}|{normalized}")


def main() -> int:
    state = load_state()

    if TEST_DISCORD:
        send_discord(
            discord_payload(
                "test",
                "Manual workflow test",
                "🧪 Teste manual concluído. GitHub Actions conseguiu usar o secret DISCORD_WEBHOOK_URL e publicar neste canal.",
                "GitHub Actions / manual dispatch",
            )
        )
        print("Discord test sent successfully")
        return 0

    items = []
    errors = []
    for fetcher in (fetch_help, fetch_community):
        try:
            items.extend(fetcher())
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {exc}")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)

    seen = set(state.get("seen_alerts", []))
    fingerprints = state.setdefault("source_fingerprints", {})
    alerts_to_send = []

    for item in items:
        level = classify(
            item["text"],
            item["raw"],
            item["username"],
            item["source_kind"],
        )
        current_fp = sha(item["text"])
        previous_fp = fingerprints.get(item["id"])
        fingerprints[item["id"]] = current_fp

        if not level:
            continue

        alert_fp = event_fingerprint(item, level)
        if alert_fp in seen:
            continue

        # First scheduled run establishes a baseline so old announcements do not
        # flood Discord immediately after installation.
        if not state.get("initialized", False):
            seen.add(alert_fp)
            continue

        # Help Center is a mutable page. Only alert from it when the relevant
        # content actually changed since the previous run.
        if item["source_kind"] == "help" and previous_fp == current_fp:
            continue

        alerts_to_send.append((item, level, alert_fp))

    if not state.get("initialized", False):
        state["initialized"] = True
        state["seen_alerts"] = sorted(seen)[-500:]
        save_state(state)
        print(f"Baseline initialized with {len(items)} source items; no alerts sent")
        return 0

    for item, level, alert_fp in alerts_to_send:
        payload = discord_payload(level, item["title"], item["text"], item["url"])
        send_discord(payload)
        seen.add(alert_fp)
        print(f"Sent {level} alert: {item['title']} ({item['url']})")

    state["seen_alerts"] = sorted(seen)[-500:]
    save_state(state)
    print(f"Checked {len(items)} source items; sent {len(alerts_to_send)} alert(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
