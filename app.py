from flask import Flask
import requests
from datetime import datetime, timedelta
import zoneinfo
import json
import os
import time

app = Flask(__name__)

local_tz = zoneinfo.ZoneInfo("Europe/Berlin")
TRACK_FILE = "tracking.json"

API_KEY = "c355d24f246aa8292a71a63932649e16"

# ---------------- CACHE ----------------

ODDS_CACHE = {
    "data": {},
    "time": 0
}

# ---------------- SAFE REQUEST ----------------

def safe_get(url):
    try:
        return requests.get(url, timeout=3).json()
    except:
        return {}

# ---------------- DATE ----------------

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

    entry = {
        "game": game,
        "player": player,
        "prob": prob,
        "date": today,
        "time": datetime.now().strftime("%H:%M"),
        "result": None
    }

    data.append(entry)
    data = data[-100:]

    with open(TRACK_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------------- AUTO RESULT ----------------

def update_results():
    data = load_tracking()
    us_date = get_us_date()

    schedule = safe_get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={us_date}")

    for entry in data:
        if entry["result"] is not None:
            continue

        for date in schedule.get("dates", []):
            for game in date.get("games", []):

                home = game["teams"]["home"]["team"]["name"]
                away = game["teams"]["away"]["team"]["name"]
                match = f"{away} vs {home}"

                if match != entry["game"]:
                    continue

                if "Final" not in game["status"]["detailedState"]:
                    continue

                game_id = game["gamePk"]

                live = safe_get(f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live")
                teams = live.get("liveData", {}).get("boxscore", {}).get("teams", {})

                for side in ["home", "away"]:
                    for p in teams.get(side, {}).get("players", {}).values():

                        if p["person"]["fullName"] == entry["player"]:
                            hits = p.get("stats", {}).get("batting", {}).get("hits", 0)
                            entry["result"] = "hit" if hits >= 1 else "miss"
                            break

    with open(TRACK_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------------- ODDS CACHE ----------------

def get_player_props_cached():
    global ODDS_CACHE

    now = time.time()

    # 5 Minuten Cache
    if now - ODDS_CACHE["time"] < 300:
        return ODDS_CACHE["data"]

    try:
        url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={API_KEY}&regions=us&markets=player_hits"

        response = requests.get(url, timeout=3)

        if response.status_code != 200:
            return ODDS_CACHE["data"]

        data = response.json()

        props = {}

        for game in data:
            home = game.get("home_team")
            away = game.get("away_team")

            key = f"{away} vs {home}"
            props[key] = {}

            for bookmaker in game.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "player_hits":
                        continue

                    for outcome in market.get("outcomes", []):
                        name = outcome.get("description")
                        price = outcome.get("price")

                        if name and price:
                            props[key][name] = price

        ODDS_CACHE["data"] = props
        ODDS_CACHE["time"] = now

        return props

    except:
        return ODDS_CACHE["data"]

# ---------------- MODEL ----------------

def get_avg(player):
    avg = player.get("stats", {}).get("batting", {}).get("avg")
    try:
        return float(avg)
    except:
        return 0.245

def simple_model(avg, lineup):
    ab = 4 if lineup <= 2 else 3.5 if lineup <= 5 else 3
    return max(0.05, min(1 - (1 - avg) ** ab, 0.95))

def confidence(prob, avg, lineup):
    score = prob * 10
    if avg > 0.300: score += 1
    if lineup <= 2: score += 1
    return round(score,1)

def is_value(prob, odds):
    return prob > (1 / odds)

# ---------------- DATA ----------------

def get_games():
    us_date = get_us_date()
    data = safe_get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={us_date}")

    all_props = get_player_props_cached()

    games = []
    status_flag = "ok"

    for date in data.get("dates", []):
        for game in date.get("games", []):

            try:
                game_id = game["gamePk"]

                home = game["teams"]["home"]["team"]["name"]
                away = game["teams"]["away"]["team"]["name"]

                game_key = f"{away} vs {home}"
                player_props = all_props.get(game_key, {})

                dt = datetime.fromisoformat(game["gameDate"].replace("Z","+00:00"))
                time_str = dt.astimezone(local_tz).strftime("%H:%M")

                status = game["status"]["detailedState"]

                live = safe_get(f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live")

                teams = live.get("liveData", {}).get("boxscore", {}).get("teams", {})

                linescore = live.get("liveData", {}).get("linescore", {})
                home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0)
                away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0)

                players_raw = []

                for side in ["home", "away"]:
                    players = list(teams.get(side, {}).get("players", {}).values())

                    for p in players:
                        order = p.get("battingOrder")
                        if not order:
                            continue

                        lineup = int(order) // 100
                        if lineup < 1 or lineup > 5:
                            continue

                        player_name = p["person"]["fullName"]

                        avg = get_avg(p)
                        prob = simple_model(avg, lineup)
                        conf = confidence(prob, avg, lineup)

                        odds = player_props.get(player_name)
                        if not odds:
                            odds = 2.0

                        value = is_value(prob, odds)

                        players_raw.append({
                            "name": player_name,
                            "prob": round(prob * 100, 1),
                            "conf": conf,
                            "lineup": lineup,
                            "odds": odds,
                            "value": value
                        })

                if players_raw:
                    players_sorted = sorted(players_raw, key=lambda x: x["conf"], reverse=True)

                    best = players_sorted[0]
                    others = players_sorted[1:3]

                    players = []

                    best["best"] = True
                    players.append(best)

                    save_pick(game_key, best["name"], best["prob"])

                    for p in others:
                        p["best"] = False
                        players.append(p)
                else:
                    players = []

                games.append({
                    "match": game_key,
                    "time": time_str,
                    "status": status,
                    "players": players,
                    "home_score": home_score,
                    "away_score": away_score
                })

            except:
                status_flag = "error"
                continue

    if not games:
        status_flag = "loading"

    return {
        "games": sorted(games, key=lambda x: x.get("time", "")),
        "status": status_flag
    }

# ---------------- WEB ----------------

@app.route("/")
def home():
    try:
        update_results()

        data = get_games()
        games = data["games"]
        status = data["status"]
        tracking = load_tracking()

        now = datetime.now(local_tz).strftime("%H:%M:%S")
        refresh_time = "10" if status != "ok" else "30"

        html = f"""
        <html>
        <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="refresh" content="{refresh_time}">
        <style>
        body {{ background:#0f172a;color:white;font-family:Arial;margin:0; }}
        .header {{ padding:15px;text-align:center;background:#020617; }}
        .card {{ background:#1e293b;margin:10px;padding:12px;border-radius:12px; }}
        .best {{ background:#16a34a;padding:10px;border-radius:10px;margin-top:8px; }}
        .alt {{ background:#334155;padding:8px;border-radius:8px;margin-top:6px; }}
        </style>
        </head>

        <body>

        <div class="header">
        🔥 MLB VALUE PICKS<br>
        <small>{now}</small>
        </div>
        """

        if status == "loading":
            html += "<p style='padding:10px;color:yellow'>🔄 Lade Daten...</p>"
        elif status == "error":
            html += "<p style='padding:10px;color:red'>⚠️ Fehler – retry...</p>"
        else:
            html += "<p style='padding:10px;color:lightgreen'>✅ Live Daten</p>"

        # TRACKING
        html += "<h3 style='padding:10px'>📊 Tracking</h3>"

        if tracking:
            wins = len([t for t in tracking if t.get("result") == "hit"])
            losses = len([t for t in tracking if t.get("result") == "miss"])
            total = len(tracking)
            rate = round((wins / total) * 100, 1) if total > 0 else 0

            html += f"<p style='padding:10px'>Hit Rate: {rate}% ({wins}-{losses})</p>"

            for t in sorted(tracking, key=lambda x: x["date"] + x["time"], reverse=True)[:10]:
                icon = "🟢" if t.get("result") == "hit" else "🔴" if t.get("result") == "miss" else "⚪"

                html += f"""
                <div class='card'>
                {icon} {t['player']} ({t['prob']}%)<br>
                {t['game']}
                </div>
                """
        else:
            html += "<p style='padding:10px'>Noch keine Daten</p>"

        # GAMES
        for g in games:
            html += f"<div class='card'><b>{g['match']}</b><br>"
            html += f"{g['time']} | {g['status']}<br>"
            html += f"⚾ {g['away_score']} : {g['home_score']}<br>"

            if not g["players"]:
                html += "⏳ Lineups noch nicht verfügbar"
            else:
                for p in g["players"]:
                    value_tag = "💰 VALUE BET" if p.get("value") else ""

                    if p.get("best"):
                        html += f"""
                        <div class='best'>
                        ⭐ {p['lineup']}. {p['name']}<br>
                        {p['prob']}% | Odds {p['odds']}<br>
                        {value_tag}
                        </div>
                        """
                    else:
                        html += f"""
                        <div class='alt'>
                        {p['lineup']}. {p['name']}<br>
                        {p['prob']}% | {p['odds']}
                        </div>
                        """

            html += "</div>"

        html += "</body></html>"
        return html

    except Exception as e:
        return f"<h1 style='color:red'>ERROR</h1><pre>{str(e)}</pre>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
