#!/usr/bin/env python3
"""
PGA Tour Leaderboard Monitor
Runs once per GitHub Actions trigger, compares against saved state, emails if changed.
"""

import requests
import base64
import gzip
import json
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── CONFIG ───────────────────────────────────────────────────────────────────

GRAPHQL_URL = "https://orchestrator.pgatour.com/graphql"
API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_PASSWORD"]
ALERT_TO = os.environ["GMAIL_USER"]

# Tournament ID — update this each week
# Find it in Chrome DevTools → Network → graphql → Payload on pgatour.com/leaderboard
# Current: R2026475 = Valspar Championship (Mar 19-22, 2026)
TOURNAMENT_ID = "R2026475"

STATE_FILE = "player_state.json"

# State transitions that are normal round progression — do NOT alert on these
IGNORED_TRANSITIONS = {
    ("NOT_STARTED", "ACTIVE"),
    ("ACTIVE", "COMPLETE"),
}

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# ─── GRAPHQL QUERY ────────────────────────────────────────────────────────────

QUERY = """
query LeaderboardCompressedV3($leaderboardCompressedV3Id: ID!) {
  leaderboardCompressedV3(id: $leaderboardCompressedV3Id) {
    id
    payload
  }
}
"""

# ─── FETCH ────────────────────────────────────────────────────────────────────

def fetch_leaderboard(tournament_id):
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "x-pgat-platform": "web",
        "Origin": "https://www.pgatour.com",
        "Referer": "https://www.pgatour.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    body = {
        "operationName": "LeaderboardCompressedV3",
        "query": QUERY,
        "variables": {"leaderboardCompressedV3Id": tournament_id},
    }
    r = requests.post(GRAPHQL_URL, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def decode_payload(b64_gzip):
    compressed = base64.b64decode(b64_gzip)
    decompressed = gzip.decompress(compressed)
    return json.loads(decompressed)


def extract_players(data):
    players = {}
    for entry in data.get("players", []):
        name = entry.get("player", {}).get("displayName", "").strip()
        state = entry.get("scoringData", {}).get("playerState", "UNKNOWN")
        if name:
            players[name] = state
    return players


# ─── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(subject, body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ALERT_TO
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, ALERT_TO, msg.as_string())
    log.info(f"Email sent: {subject}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    # Fetch current leaderboard
    raw = fetch_leaderboard(TOURNAMENT_ID)
    b64 = raw["data"]["leaderboardCompressedV3"]["payload"]
    decoded = decode_payload(b64)
    current_players = extract_players(decoded)
    log.info(f"Players on leaderboard: {len(current_players)}")

    # Load previous state
    if not os.path.exists(STATE_FILE):
        log.info("No previous state found — saving baseline.")
        with open(STATE_FILE, "w") as f:
            json.dump(current_players, f)
        return

    with open(STATE_FILE) as f:
        previous_players = json.load(f)

    # Diff
    prev_names = set(previous_players.keys())
    curr_names = set(current_players.keys())

    added   = curr_names - prev_names
    removed = prev_names - curr_names

    # Filter out normal round progression from state changes
    state_changes = {
        name: (previous_players[name], current_players[name])
        for name in prev_names & curr_names
        if previous_players[name] != current_players[name]
        and (previous_players[name], current_players[name]) not in IGNORED_TRANSITIONS
    }

    if added or removed or state_changes:
        lines = []
        for name in sorted(added):
            lines.append(f"Player Added - {name}")
        for name in sorted(removed):
            lines.append(f"Player Removed - {name}")
        for name, (old, new) in sorted(state_changes.items()):
            lines.append(f"MISC. - {name}: {old} → {new}")

        body = "\n".join(lines)
        log.info(body)
        send_email("PGA Tour Alert", body)
    else:
        log.info("No changes.")

    # Save updated state
    with open(STATE_FILE, "w") as f:
        json.dump(current_players, f)


if __name__ == "__main__":
    main()
