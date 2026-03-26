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

# ---------------- ADAPTIVE ----------------

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

                players_raw = []
                probs = []

                for side in ["home","away"]:
                    for p in teams_live.get(side, {}).get("players", {}).values():

                        order = p.get("battingOrder")

                        # FIX → fallback lineup
                        lineup = int(order)//100 if order else 9

                        avg = p.get("stats",{}).get("batting",{}).get("avg")
                        if not avg:
                            continue

                        try:
                            avg = float(avg)
                        except:
                            continue

                        if side == "home":
                            prob = pro_model(avg, lineup, away_era, 1.25, away_hand)
                        else:
                            prob = pro_model(avg, lineup, home_era, 1.25, home_hand)

                        conf = confidence(prob, avg, lineup)

                        players_raw.append({
                            "name": p["person"]["fullName"],
                            "prob": round(prob*100,1),
                            "raw_prob": prob,
                            "conf": conf,
                            "lineup": lineup
                        })

                        probs.append(prob)

                threshold = adaptive_threshold(probs)

                players = []

                # LEVEL 1
                for p in players_raw:
                    if p["raw_prob"] >= threshold:
                        p["fallback"] = p["raw_prob"] < 0.55
                        p["lock"] = p["raw_prob"] >= 0.65 and p["conf"] >= 8
                        players.append(p)

                # LEVEL 2
                if len(players) < 2:
                    fallback_players = sorted(players_raw, key=lambda x: x["conf"], reverse=True)
                    for p in fallback_players:
                        if p not in players:
                            p["fallback"] = True
                            p["lock"] = False
                            players.append(p)
                        if len(players) >= 3:
                            break

                # LEVEL 3
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
    now = datetime.now(local_tz).strftime("%H:%M:%S")

    all_players = [p for g in games for p in g["players"]]
    top_players = sorted(all_players, key=lambda x: x["conf"], reverse=True)[:5]
    locks = [p for p in all_players if p.get("lock")]

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

    .elite {{ background:#065f46; }}
    .solid {{ background:#78350f; }}
    .risky {{ background:#7f1d1d; }}

    .lock-card {{ background:#065f46;margin:10px;padding:12px;border-radius:12px; }}
    .top-card {{ background:#1d4ed8;margin:10px;padding:12px;border-radius:12px; }}

    .live {{ color:#22c55e; }}
    .upcoming {{ color:#facc15; }}
    .final {{ color:#94a3b8; }}
    </style>
    </head>

    <body>

    <div class="header">
    🔥 MLB ELITE APP<br>
    <small>Last update: {now}</small>
    </div>
    """

    # LOCK PICKS
    html += "<h3 style='padding:10px'>🔒 LOCK PICKS</h3>"
    if not locks:
        html += "<p style='padding:10px'>Keine sicheren Picks</p>"
    else:
        for p in locks:
            html += f"<div class='lock-card'>🔒 {p['name']} {p['prob']}% ⭐ {p['conf']}</div>"

    # TOP PICKS
    html += "<h3 style='padding:10px'>🔥 TOP PICKS</h3>"
    for p in top_players:
        html += f"<div class='top-card'>{p['name']} {p['prob']}% ⭐ {p['conf']}</div>"

    # GAMES
    html += "<h3 style='padding:10px'>⚾ GAMES</h3>"

    for g in games:

        if "Live" in g["status"]:
            status_class = "live"
        elif "Final" in g["status"]:
            status_class = "final"
        else:
            status_class = "upcoming"

        html += f"<div class='card'><b>{g['match']}</b><br>"
        html += f"<span class='{status_class}'>{g['time']} | {g['status']}</span><br>"
        html += f"⚾ {g['away_score']} : {g['home_score']}<br>"
        html += f"⏱️ {g['inning']}<br>"
        html += f"🏠 {g['home_pitcher']}<br>"
        html += f"✈️ {g['away_pitcher']}<br>"

        # 🔥 FINAL FIX → IMMER anzeigen
        if not g["players"]:
            html += "No good pick"
        else:
            for p in g["players"]:

                if p["conf"] >= 9:
                    cls = "elite"
                elif p["conf"] >= 7:
                    cls = "solid"
                else:
                    cls = "risky"

                label = "⚠️ fallback" if p.get("fallback") else ""
                lock = "🔒" if p.get("lock") else ""

                html += f"<div class='{cls}' style='padding:6px;margin-top:6px;border-radius:8px;'>"
                html += f"{lock} {p['lineup']}. {p['name']}<br>"
                html += f"{p['prob']}% ⭐ {p['conf']} {label}</div>"

        html += "</div>"

    html += "</body></html>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
