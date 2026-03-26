from flask import Flask
import requests
from datetime import datetime
import zoneinfo

app = Flask(__name__)

local_tz = zoneinfo.ZoneInfo("Europe/Berlin")

# ---------------- SAFE REQUEST ----------------

def safe_get(url):
    try:
        return requests.get(url, timeout=3).json()
    except:
        return {}

# ---------------- MODEL ----------------

def get_avg(player):
    avg = player.get("stats", {}).get("batting", {}).get("avg")
    try:
        return float(avg)
    except:
        return 0.245

def simple_model(avg, lineup):
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
    data = safe_get(url)

    games = []
    status_flag = "ok"

    for date in data.get("dates", []):
        for game in date.get("games", []):

            try:
                game_id = game["gamePk"]

                home = game["teams"]["home"]["team"]["name"]
                away = game["teams"]["away"]["team"]["name"]

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
                    players = list(teams.get(side, {}).get("players", {}).values())[:6]

                    for p in players:
                        lineup = int(p.get("battingOrder", "900")) // 100
                        avg = get_avg(p)

                        prob = simple_model(avg, lineup)
                        conf = confidence(prob, avg, lineup)

                        players_raw.append({
                            "name": p["person"]["fullName"],
                            "prob": round(prob * 100, 1),
                            "conf": conf,
                            "lineup": lineup
                        })

                # 🔥 BEST PICK SYSTEM
                players_sorted = sorted(players_raw, key=lambda x: x["conf"], reverse=True)

                best = players_sorted[0] if players_sorted else None
                others = players_sorted[1:3] if len(players_sorted) > 1 else []

                players = []

                if best:
                    best["best"] = True
                    players.append(best)

                for p in others:
                    p["best"] = False
                    players.append(p)

                games.append({
                    "match": f"{away} vs {home}",
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
        data = get_games()
        games = data["games"]
        status = data["status"]

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

        .best {{
            background:#16a34a;
            padding:10px;
            border-radius:10px;
            margin-top:8px;
            font-weight:bold;
        }}

        .alt {{
            background:#334155;
            padding:8px;
            border-radius:8px;
            margin-top:6px;
        }}
        </style>
        </head>

        <body>

        <div class="header">
        🔥 MLB LIVE PICKS<br>
        <small>{now}</small>
        </div>
        """

        # STATUS
        if status == "loading":
            html += "<p style='padding:10px;color:yellow'>🔄 Lade Daten...</p>"
        elif status == "error":
            html += "<p style='padding:10px;color:red'>⚠️ Fehler – retry...</p>"
        else:
            html += "<p style='padding:10px;color:lightgreen'>✅ Live Daten</p>"

        # CONTENT
        if not games:
            html += "<p style='padding:10px'>Keine Spiele gefunden</p>"

        for g in games:
            html += f"<div class='card'><b>{g['match']}</b><br>"
            html += f"{g['time']} | {g['status']}<br>"
            html += f"⚾ {g['away_score']} : {g['home_score']}<br>"

            if not g["players"]:
                html += "⚠️ No picks yet"
            else:
                for p in g["players"]:
                    if p.get("best"):
                        html += f"""
                        <div class="best">
                        ⭐ BEST PICK<br>
                        {p['lineup']}. {p['name']}<br>
                        {p['prob']}% Trefferchance<br>
                        🔥 HIGH CONFIDENCE
                        </div>
                        """
                    else:
                        html += f"""
                        <div class="alt">
                        {p['lineup']}. {p['name']}<br>
                        {p['prob']}%
                        </div>
                        """

            html += "</div>"

        html += "</body></html>"
        return html

    except Exception as e:
        return f"<h1 style='color:red'>ERROR</h1><pre>{str(e)}</pre>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
