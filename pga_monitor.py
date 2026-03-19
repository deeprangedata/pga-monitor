#!/usr/bin/env python3
"""
PGA Tour Leaderboard Monitor
Polls the PGA Tour GraphQL API every 60 seconds.
Sends email alerts when players are added or removed from the leaderboard.
"""

import requests
import base64
import gzip
import json
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ─── CONFIG ───────────────────────────────────────────────────────────────────

POLL_INTERVAL = 60  # seconds

# PGA Tour GraphQL
GRAPHQL_URL = "https://orchestrator.pgatour.com/graphql"
API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"

# Gmail
GMAIL_USER = "jhausknecht07@gmail.com"
GMAIL_APP_PASSWORD = "qizb grdv zjsp dlzp"
ALERT_TO = "jhausknecht07@gmail.com"

# Tournament ID — update this each week
# Find it in Chrome DevTools → Network → graphql → Payload on pgatour.com/leaderboard
# Current: R2026475 = Valspar Championship (Mar 19-22, 2026)
TOURNAMENT_ID = "R2026475"

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
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
    try:
        r = requests.post(GRAPHQL_URL, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Fetch error: {e}")
        return None


def decode_payload(b64_gzip):
    """Base64-decode then gzip-decompress the payload field."""
    try:
        compressed = base64.b64decode(b64_gzip)
        decompressed = gzip.decompress(compressed)
        return json.loads(decompressed)
    except Exception as e:
        log.error(f"Decode error: {e}")
        return None


def extract_players(data):
    """
    Extract players from the leaderboard payload.
    Returns a dict of { displayName: playerState }
    e.g. { "Scottie Scheffler": "ACTIVE", "Jon Rahm": "WD" }
    """
    players = {}
    for entry in data.get("players", []):
        name = entry.get("player", {}).get("displayName", "").strip()
        state = entry.get("scoringData", {}).get("playerState", "UNKNOWN")
        if name:
            players[name] = state
    return players


# ─── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(subject, body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = GMAIL_USER
        msg["To"] = ALERT_TO
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, ALERT_TO, msg.as_string())

        log.info(f"Email sent: {subject}")
    except Exception as e:
        log.error(f"Email error: {e}")


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    log.info(f"Starting PGA Tour monitor — tournament {TOURNAMENT_ID}")
    log.info(f"Polling every {POLL_INTERVAL}s  →  alerts to {ALERT_TO}")

    previous_players = None

    while True:
        raw = fetch_leaderboard(TOURNAMENT_ID)

        if raw is None:
            log.warning("No response — skipping this cycle")
            time.sleep(POLL_INTERVAL)
            continue

        try:
            b64 = raw["data"]["leaderboardCompressedV3"]["payload"]
        except (KeyError, TypeError) as e:
            log.warning(f"Unexpected response shape: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        decoded = decode_payload(b64)
        if decoded is None:
            time.sleep(POLL_INTERVAL)
            continue

        current_players = extract_players(decoded)
        log.info(f"Players on leaderboard: {len(current_players)}")

        if previous_players is None:
            log.info("Baseline snapshot established:")
            for name, state in sorted(current_players.items()):
                log.info(f"  {name} ({state})")
            previous_players = current_players
            time.sleep(POLL_INTERVAL)
            continue

        # Detect additions and removals
        prev_names = set(previous_players.keys())
        curr_names = set(current_players.keys())

        added   = curr_names - prev_names
        removed = prev_names - curr_names

        # Detect state changes (e.g. NOT_STARTED → WD)
        state_changes = {
            name: (previous_players[name], current_players[name])
            for name in prev_names & curr_names
            if previous_players[name] != current_players[name]
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
            subject = "PGA Tour Alert"

            log.info(subject)
            print(body)
            send_email(subject, body)

            previous_players = current_players
        else:
            log.info("No changes.")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
