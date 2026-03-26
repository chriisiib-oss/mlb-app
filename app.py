from flask import Flask
import requests
from datetime import datetime
import zoneinfo

app = Flask(__name__)

# -------- TIMEZONE (GLOBAL) --------
local_tz = zoneinfo.ZoneInfo("Europe/Berlin")

# ---------------- MODEL ----------------

def estimate_xba(avg):
    return avg * 0.7 + 0.245 * 0.3

def x_boost(avg):
    xba = estimate_xba(avg)
    if xba - avg > 0.015:
        return 1.08
    elif avg - xba > 0.015:
        return 0.92
    return 1.0

def split_boost(avg, hand):
    if hand == "L":
        return 1.05 if avg > 0.270 else 0.98
    return 1.03 if avg > 0.270 else 0.99

def pro_model(avg, lineup_pos, era, whip, hand):
    ab = 4 if lineup_pos <= 5 else 3
    base = 1 - (1 - avg) ** ab

    pitcher_adj = 1.0
    if era > 4.5: pitcher_adj += 0.18
    elif era < 3.2: pitcher_adj -= 0.18

    if whip > 1.35: pitcher_adj += 0.10
    elif whip < 1.05: pitcher_adj -= 0.10

    batter_adj = 1.12 if avg > 0.300 else (0.88 if avg < 0.220 else 1.0)

    return max(0.05, min(base * pitcher_adj * batter_adj * split_boost(avg, hand) * x_boost(avg), 0.95))

def confidence(prob, avg, lineup):
    score = prob * 10
    if avg > 0.300: score += 1
    if lineup <= 3: score += 1
    if prob > 0.7: score += 1
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

                # ✅ richtige lokale Zeit
                dt = datetime.fromisoformat(game["gameDate"].replace("Z","+00:00"))
                local_time = dt.astimezone(local_tz)
                time_str = local_time.strftime("%H:%M")

                status = game["status"]["detailedState"]

                # LIVE DATA
                live = requests.get(
                    f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live",
                    timeout=5
                ).json()

                teams_live = live.get("liveData", {}).get("boxscore", {}).get("teams", {})

                # -------- SCORE --------
                linescore = live.get("liveData", {}).get("linescore", {})

                home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0)
                away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0)

                inning = linescore.get("currentInning", "")
                half = linescore.get("inningHalf", "")
                inning_text = f"{half} {inning}" if inning else ""

                # -------- PITCHER --------
                home_pitcher, away_pitcher = "?", "?"
                home_era, away_era = 4.2, 4.2
                home_hand, away_hand = "R", "R"

                try:
                    hp = teams_live["home"]["pitchers"][0]
                    p = teams_live["home"]["players"][f"ID{hp}"]
                    home_pitcher = p["person"]["fullName"]
                    home_hand = p.get("pitchHand", {}).get("code","R")
                    home_era = float(p.get("stats",{}).get("pitching",{}).get("era",4.2))
                except:
                    pass

                try:
                    ap = teams_live["away"]["pitchers"][0]
                    p = teams_live["away"]["players"][f"ID{ap}"]
                    away_pitcher = p["person"]["fullName"]
                    away_hand = p.get("pitchHand", {}).get("code","R")
                    away_era = float(p.get("stats",{}).get("pitching",{}).get("era",4.2))
                except:
                    pass

                players = []
                has_lineup = False

                for side in ["home","away"]:
                    for p in teams_live.get(side, {}).get("players", {}).values():

                        order = p.get("battingOrder")
                        if not order:
                            continue

                        has_lineup = True
                        lineup = int(order)//100

                        avg = p.get("stats",{}).get("batting",{}).get("avg")
                        if not avg:
                            continue

                        try:
                            avg = float(avg)
                        except:
                            continue

                        # Gegner Pitcher berücksichtigen
                        if side == "home":
                            prob = pro_model(avg, lineup, away_era, 1.25, away_hand)
                        else:
                            prob = pro_model(avg, lineup, home_era, 1.25, home_hand)

                        conf = confidence(prob, avg, lineup)

                        if prob >= 0.55:
                            players.append({
                                "name": p["person"]["fullName"],
                                "prob": round(prob*100,1),
                                "conf": conf,
                                "lineup": lineup
                            })

                players = sorted(players, key=lambda x: x["conf"], reverse=True)[:3]

                games.append({
                    "match": f"{away} vs {home}",
                    "time": time_str,
                    "status": status,
                    "players": players,
                    "has_lineup": has_lineup,
                    "home_pitcher": home_pitcher,
                    "away_pitcher": away_pitcher,
                    "home_score": home_score,
                    "away_score": away_score,
                    "inning": inning_text
                })

            except:
                continue

    return sorted(games, key=lambda x: x["time"])

# ---------------- WEB ----------------

@app.route("/")
def home():
    games = get_games()

    # ✅ richtige deutsche Zeit
    now = datetime.now(local_tz).strftime("%H:%M:%S")

    html = f"""
    <html>
    <head>
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="30">

    <style>
    body {{ background:#0f172a;color:white;font-family:Arial;margin:0; }}
    .header {{ padding:15px;text-align:center;background:#020617; }}
    .card {{ background:#1e293b;margin:10px;padding:12px;border-radius:12px; }}
    .live {{ color:#22c55e; }}
    .upcoming {{ color:#facc15; }}
    .final {{ color:#94a3b8; }}
    </style>
    </head>

    <body>

    <div class="header">
    🔥 MLB LIVE APP<br>
    <small>Last update: {now}</small>
    </div>
    """

    for g in games:

        if "Live" in g["status"]:
            status_class = "live"
        elif "Final" in g["status"]:
            status_class = "final"
        else:
            status_class = "upcoming"

        html += f"""
        <div class="card">
        <b>{g['match']}</b><br>
        <span class="{status_class}">{g['time']} | {g['status']}</span><br>

        ⚾ {g['away_score']} : {g['home_score']}<br>
        ⏱️ {g['inning']}<br>

        🏠 {g['home_pitcher']}<br>
        ✈️ {g['away_pitcher']}<br>
        """

        if not g["has_lineup"]:
            html += "Waiting for lineups..."
        elif not g["players"]:
            html += "No good pick"
        else:
            for p in g["players"]:
                html += f"{p['lineup']}. {p['name']} → {p['prob']}% ⭐ {p['conf']}<br>"

        html += "</div>"

    html += "</body></html>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
