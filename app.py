from flask import Flask
import requests
from datetime import datetime
import zoneinfo
import time

app = Flask(__name__)

local_tz = zoneinfo.ZoneInfo("Europe/Berlin")

# ---------------- CACHE ----------------

last_season_cache = {}
h2h_cache = {}

CACHE_EXPIRY = 3600  # 1 Stunde

# ---------------- MODEL ----------------

def get_last_season_avg(player_id):
    now = time.time()

    if player_id in last_season_cache:
        val, ts = last_season_cache[player_id]
        if now - ts < CACHE_EXPIRY:
            return val

    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&season=2025"
        data = requests.get(url, timeout=2).json()
        splits = data.get("stats", [])[0].get("splits", [])
        if splits:
            avg = float(splits[0]["stat"].get("avg", 0.245))
            last_season_cache[player_id] = (avg, now)
            return avg
    except:
        pass

    return 0.245


def h2h_adjustment(batter_id, pitcher_id):
    key = f"{batter_id}_{pitcher_id}"
    now = time.time()

    if key in h2h_cache:
        val, ts = h2h_cache[key]
        if now - ts < CACHE_EXPIRY:
            return val

    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{batter_id}/stats?stats=vsPlayer&opposingPlayerId={pitcher_id}"
        data = requests.get(url, timeout=2).json()

        splits = data.get("stats", [])[0].get("splits", [])
        if splits:
            stat = splits[0]["stat"]
            avg = float(stat.get("avg", 0))
            pa = stat.get("plateAppearances", 0)

            adj = 1.0
            if pa >= 5:
                if avg >= 0.300:
                    adj = 1.12
                elif avg <= 0.180:
                    adj = 0.88

            h2h_cache[key] = (adj, now)
            return adj
    except:
        pass

    return 1.0


def hybrid_avg(player_id, current_avg, games_played):
    last_avg = get_last_season_avg(player_id)

    try:
        current_avg = float(current_avg)
    except:
        current_avg = None

    if games_played < 5:
        w = 0.2
    elif games_played < 15:
        w = 0.4
    else:
        w = 0.65

    if current_avg is None:
        return last_avg

    return (current_avg * w) + (last_avg * (1 - w))


def pro_model(avg, lineup):
    ab = 4 if lineup <= 5 else 3
    return max(0.05, min(1 - (1 - avg) ** ab, 0.95))


def confidence(prob, avg, lineup):
    score = prob * 10
    if avg > 0.300: score += 1
    if lineup <= 3: score += 1
    return round(score,1)


# ---------------- DATA ----------------

def get_games():
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
    data = requests.get(url).json()

    games = []

    for date in data.get("dates", []):
        for game in date.get("games", []):

            try:
                game_id = game["gamePk"]

                home = game["teams"]["home"]["team"]["name"]
                away = game["teams"]["away"]["team"]["name"]

                dt = datetime.fromisoformat(game["gameDate"].replace("Z","+00:00"))
                time_str = dt.astimezone(local_tz).strftime("%H:%M")

                status = game["status"]["detailedState"]

                live = requests.get(
                    f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live",
                    timeout=3
                ).json()

                teams_live = live.get("liveData", {}).get("boxscore", {}).get("teams", {})

                linescore = live.get("liveData", {}).get("linescore", {})
                home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0)
                away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0)

                # Pitcher IDs
                home_p = teams_live.get("home", {}).get("pitchers", [])
                away_p = teams_live.get("away", {}).get("pitchers", [])

                home_pid = home_p[0] if home_p else None
                away_pid = away_p[0] if away_p else None

                players_raw = []

                # 🔥 LIMIT → MAX 12 Spieler
                for side in ["home","away"]:
                    players = list(teams_live.get(side, {}).get("players", {}).values())[:6]

                    for p in players:

                        lineup = int(p.get("battingOrder", "900")) // 100
                        player_id = p["person"]["id"]

                        current_avg = p.get("stats",{}).get("batting",{}).get("avg")
                        games_played = p.get("stats",{}).get("batting",{}).get("gamesPlayed", 0)

                        avg = hybrid_avg(player_id, current_avg, games_played)

                        pitcher_id = away_pid if side == "home" else home_pid
                        h2h = h2h_adjustment(player_id, pitcher_id) if pitcher_id else 1.0

                        prob = pro_model(avg, lineup) * h2h
                        conf = confidence(prob, avg, lineup)

                        players_raw.append({
                            "name": p["person"]["fullName"],
                            "prob": round(prob*100,1),
                            "conf": conf,
                            "lineup": lineup
                        })

                # 🔥 immer Top 3 garantieren
                players = sorted(players_raw, key=lambda x: x["conf"], reverse=True)[:3]

                games.append({
                    "match": f"{away} vs {home}",
                    "time": time_str,
                    "status": status,
                    "players": players,
                    "home_score": home_score,
                    "away_score": away_score
                })

            except:
                continue

    return sorted(games, key=lambda x: x["time"])


# ---------------- WEB ----------------

@app.route("/")
def home():
    games = get_games()
    now = datetime.now(local_tz).strftime("%H:%M:%S")

    html = f"""
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="30">
    <style>
    body {{ background:#0f172a;color:white;font-family:Arial;margin:0; }}
    .card {{ background:#1e293b;margin:10px;padding:12px;border-radius:12px; }}
    .header {{ padding:15px;text-align:center;background:#020617; }}
    </style>
    </head>
    <body>

    <div class="header">
    🔥 MLB APP<br>
    {now}
    </div>
    """

    for g in games:
        html += f"<div class='card'><b>{g['match']}</b><br>"
        html += f"{g['time']} | {g['status']}<br>"
        html += f"{g['away_score']} : {g['home_score']}<br>"

        for p in g["players"]:
            html += f"{p['lineup']}. {p['name']} {p['prob']}% ⭐{p['conf']}<br>"

        html += "</div>"

    html += "</body></html>"
    return html


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
