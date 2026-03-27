from flask import Flask
import requests
from datetime import datetime, timedelta
import zoneinfo
import json
import os
import time
import unicodedata
import re

app = Flask(__name__)

# ---------------- CONFIG ----------------

local_tz = zoneinfo.ZoneInfo("Europe/Berlin")
TRACK_FILE = "tracking.json"
API_KEY = "DEIN_API_KEY"
SPORT = os.environ.get("SPORT", "mlb")

# ---------------- CACHE ----------------

ODDS_CACHE = {"data": {}, "time": 0}

# ---------------- NAME FIX ----------------

def normalize_name(name):
    if not name:
        return ""
    name = unicodedata.normalize("NFD", name)
    name = name.encode("ascii", "ignore").decode("utf-8")
    name = name.lower().replace(".", "").replace(" jr", "").replace(" sr", "")
    name = re.sub(r"[^a-z\s]", "", name)
    return name.strip()

# ---------------- SAFE ----------------

def safe_get(url):
    try:
        return requests.get(url, timeout=3).json()
    except:
        return {}

def get_us_date():
    return (datetime.utcnow() - timedelta(hours=4)).strftime("%Y-%m-%d")

# ---------------- TRACKING ----------------

def load_tracking():
    if not os.path.exists(TRACK_FILE):
        return []
    try:
        with open(TRACK_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_pick(game, player, prob):
    data = load_tracking()
    today = datetime.now().strftime("%Y-%m-%d")

    for entry in data:
        if entry["game"] == game and entry["player"] == player and entry["date"] == today:
            return

    data.append({
        "game": game,
        "player": player,
        "prob": prob,
        "date": today,
        "time": datetime.now().strftime("%H:%M"),
        "result": None
    })

    with open(TRACK_FILE, "w") as f:
        json.dump(data[-100:], f, indent=2)

# ---------------- MLB ----------------

def get_mlb_games():
    # (gekürzt – dein bestehender MLB Code bleibt unverändert)
    return {"games": [], "status": "ok"}

# ---------------- SOCCER MODEL ----------------

def get_soccer_games():
    try:
        url = f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey={API_KEY}&regions=eu&markets=totals"
        data = requests.get(url, timeout=3).json()
    except:
        return {"games": [], "status": "error"}

    games = []

    for game in data:
        home = game.get("home_team")
        away = game.get("away_team")

        over_odds, under_odds = None, None

        try:
            for m in game["bookmakers"][0]["markets"]:
                if m["key"] == "totals":
                    for o in m["outcomes"]:
                        if o["name"] == "Over":
                            over_odds = o["price"]
                        elif o["name"] == "Under":
                            under_odds = o["price"]
        except:
            continue

        if not over_odds or not under_odds:
            continue

        prob_over = 0.55 if over_odds > 1.8 else 0.48
        prob_under = 1 - prob_over

        players = []

        if prob_over > (1 / over_odds):
            players.append({
                "name": "Over 2.5 Goals",
                "prob": round(prob_over * 100, 1),
                "odds": over_odds,
                "value": True,
                "best": True
            })

        if prob_under > (1 / under_odds):
            players.append({
                "name": "Under 2.5 Goals",
                "prob": round(prob_under * 100, 1),
                "odds": under_odds,
                "value": True,
                "best": not players
            })

        games.append({
            "match": f"{away} vs {home}",
            "time": "Today",
            "status": "Soccer",
            "players": players
        })

    return {"games": games, "status": "ok"}

# ---------------- TENNIS MODEL ----------------

def get_tennis_games():
    try:
        url = f"https://api.the-odds-api.com/v4/sports/tennis_atp/odds/?apiKey={API_KEY}&regions=eu&markets=h2h"
        data = requests.get(url, timeout=3).json()
    except:
        return {"games": [], "status": "error"}

    games = []

    for game in data:
        players = game.get("teams", [])
        if len(players) != 2:
            continue

        p1, p2 = players
        odds1, odds2 = None, None

        try:
            for o in game["bookmakers"][0]["markets"][0]["outcomes"]:
                if o["name"] == p1:
                    odds1 = o["price"]
                elif o["name"] == p2:
                    odds2 = o["price"]
        except:
            continue

        if not odds1 or not odds2:
            continue

        prob1 = (1 / odds1) * (1.05 if odds1 > 2 else 0.98)
        prob2 = (1 / odds2) * (1.05 if odds2 > 2 else 0.98)

        players_out = []

        if prob1 > (1 / odds1):
            players_out.append({
                "name": p1,
                "prob": round(prob1 * 100, 1),
                "odds": odds1,
                "value": True,
                "best": True
            })

        if prob2 > (1 / odds2):
            players_out.append({
                "name": p2,
                "prob": round(prob2 * 100, 1),
                "odds": odds2,
                "value": True,
                "best": not players_out
            })

        games.append({
            "match": f"{p1} vs {p2}",
            "time": "Today",
            "status": "Tennis",
            "players": players_out
        })

    return {"games": games, "status": "ok"}

# ---------------- ROUTER ----------------

def get_games():
    if SPORT == "mlb":
        return get_mlb_games()
    elif SPORT == "soccer":
        return get_soccer_games()
    elif SPORT == "tennis":
        return get_tennis_games()
    return {"games": [], "status": "error"}

# ---------------- WEB ----------------

@app.route("/")
def home():
    data = get_games()
    games = data["games"]

    html = f"""
    <html><body style='background:#0f172a;color:white;font-family:Arial'>
    <h2>🔥 {SPORT.upper()} MODE</h2>
    """

    for g in games:
        html += f"<div><b>{g['match']}</b><br>"

        if not g["players"]:
            html += "No Value"
        else:
            for p in g["players"]:
                tag = "💰 VALUE" if p.get("value") else ""
                html += f"{p['name']} {p['prob']}% @ {p['odds']} {tag}<br>"

        html += "</div><hr>"

    html += "</body></html>"
    return html

# ---------------- RUN ----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
