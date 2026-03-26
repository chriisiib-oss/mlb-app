from flask import Flask
import requests
from datetime import datetime
import zoneinfo

app = Flask(__name__)

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

def h2h_adjustment(batter_id, pitcher_id):
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{batter_id}/stats?stats=vsPlayer&opposingPlayerId={pitcher_id}"
        data = requests.get(url, timeout=3).json()
        splits = data.get("stats", [])[0].get("splits", [])

        if splits:
            stat = splits[0]["stat"]
            avg = float(stat.get("avg", 0))
            pa = stat.get("plateAppearances", 0)

            if pa >= 5:
                if avg >= 0.300:
                    return 1.15
                elif avg <= 0.180:
                    return 0.85
    except:
        pass

    return 1.0

def get_last_season_avg(player_id):
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&season=2025"
        data = requests.get(url, timeout=3).json()
        splits = data.get("stats", [])[0].get("splits", [])
        if splits:
            return float(splits[0]["stat"].get("avg", 0.240))
    except:
        pass
    return 0.240

def hybrid_avg(player_id, current_avg, games_played):
    last_avg = get_last_season_avg(player_id)

    try:
        current_avg = float(current_avg)
    except:
        current_avg = None

    if games_played < 5:
        w_current = 0.2
    elif games_played < 15:
        w_current = 0.4
    elif games_played < 30:
        w_current = 0.6
    else:
        w_current = 0.75

    w_last = 1 - w_current

    if current_avg is None:
        return last_avg

    return (current_avg * w_current) + (last_avg * w_last)

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

def adaptive_threshold(probs):
    if not probs:
        return 0.48
    if len([p for p in probs if p >= 0.60]) >= 3:
        return 0.60
    if len([p for p in probs if p >= 0.55]) >= 3:
        return 0.55
    if len([p for p in probs if p >= 0.50]) >= 3:
        return 0.50
    return 0.48

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
                local_time = dt.astimezone(local_tz)
                time_str = local_time.strftime("%H:%M")

                status = game["status"]["detailedState"]

                live = requests.get(
                    f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live",
                    timeout=5
                ).json()

                teams_live = live.get("liveData", {}).get("boxscore", {}).get("teams", {})

                # SCORE
                linescore = live.get("liveData", {}).get("linescore", {})
                home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0)
                away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0)

                inning = linescore.get("currentInning", "")
                half = linescore.get("inningHalf", "")
                inning_text = f"{half} {inning}" if inning else ""

                # Pitcher
                home_pitcher_id = None
                away_pitcher_id = None

                try:
                    home_pitcher_id = teams_live["home"]["pitchers"][0]
                except:
                    pass

                try:
                    away_pitcher_id = teams_live["away"]["pitchers"][0]
                except:
                    pass

                players_raw = []
                probs = []

                for side in ["home","away"]:
                    for p in teams_live.get(side, {}).get("players", {}).values():

                        lineup = int(p.get("battingOrder", "900")) // 100

                        player_id = p["person"]["id"]

                        current_avg = p.get("stats",{}).get("batting",{}).get("avg")
                        games_played = p.get("stats",{}).get("batting",{}).get("gamesPlayed", 0)

                        avg = hybrid_avg(player_id, current_avg, games_played)

                        if side == "home":
                            pitcher_id = away_pitcher_id
                        else:
                            pitcher_id = home_pitcher_id

                        h2h = h2h_adjustment(player_id, pitcher_id) if pitcher_id else 1.0

                        prob = pro_model(avg, lineup, 4.2, 1.25, "R") * h2h

                        conf = confidence(prob, avg, lineup)

                        players_raw.append({
                            "name": p["person"]["fullName"],
                            "prob": round(prob*100,1),
                            "raw_prob": prob,
                            "conf": conf,
                            "lineup": lineup,
                            "h2h": h2h
                        })

                        probs.append(prob)

                threshold = adaptive_threshold(probs)

                players = []

                for p in players_raw:
                    if p["raw_prob"] >= threshold:
                        p["fallback"] = p["raw_prob"] < 0.55
                        p["lock"] = p["raw_prob"] >= 0.65 and p["conf"] >= 8
                        players.append(p)

                if len(players) < 2:
                    fallback_players = sorted(players_raw, key=lambda x: x["conf"], reverse=True)
                    for p in fallback_players:
                        if p not in players:
                            p["fallback"] = True
                            p["lock"] = False
                            players.append(p)
                        if len(players) >= 3:
                            break

                if len(players) == 0 and players_raw:
                    players = sorted(players_raw, key=lambda x: x["conf"], reverse=True)[:3]
                    for p in players:
                        p["fallback"] = True
                        p["lock"] = False

                players = sorted(players, key=lambda x: x["conf"], reverse=True)[:3]

                games.append({
                    "match": f"{away} vs {home}",
                    "time": time_str,
                    "status": status,
                    "players": players,
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
    now = datetime.now(local_tz).strftime("%H:%M:%S")

    all_players = [p for g in games for p in g["players"]]
    top_players = sorted(all_players, key=lambda x: x["conf"], reverse=True)[:5]
    locks = [p for p in all_players if p.get("lock")]

    html = f"""
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="30">
    <style>
    body {{ background:#0f172a;color:white;font-family:Arial;margin:0; }}
    .header {{ padding:15px;text-align:center;background:#020617; }}
    .card {{ background:#1e293b;margin:10px;padding:12px;border-radius:12px; }}
    .top-card {{ background:#1d4ed8;margin:10px;padding:12px;border-radius:12px; }}
    .lock-card {{ background:#065f46;margin:10px;padding:12px;border-radius:12px; }}
    </style>
    </head>
    <body>

    <div class="header">
    🔥 MLB ELITE APP<br>
    <small>{now}</small>
    </div>
    """

    html += "<h3>🔒 LOCK PICKS</h3>"
    for p in locks:
        html += f"<div class='lock-card'>{p['name']} {p['prob']}%</div>"

    html += "<h3>🔥 TOP PICKS</h3>"
    for p in top_players:
        html += f"<div class='top-card'>{p['name']} {p['prob']}%</div>"

    html += "<h3>⚾ GAMES</h3>"

    for g in games:
        html += f"<div class='card'><b>{g['match']}</b><br>"
        html += f"{g['time']} | {g['status']}<br>"
        html += f"{g['away_score']} : {g['home_score']}<br>"
        html += f"{g['inning']}<br>"

        for p in g["players"]:
            tag = "🔥" if p["h2h"] > 1.05 else ""
            html += f"{p['lineup']}. {p['name']} {p['prob']}% {tag}<br>"

        html += "</div>"

    html += "</body></html>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
