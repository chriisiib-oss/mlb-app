from flask import Flask
import requests
import json
import os
from datetime import datetime, timezone

app = Flask(__name__)

history_file = "history.json"

# ---------------- HISTORY ----------------

def load_history():
    if not os.path.exists(history_file):
        return []
    with open(history_file, "r") as f:
        return json.load(f)

def save_history(data):
    with open(history_file, "w") as f:
        json.dump(data, f)

def calculate_accuracy():
    history = load_history()
    finished = [h for h in history if h["result"] is not None]

    if not finished:
        return 0

    wins = sum(1 for h in finished if h["result"] == 1)
    return round((wins / len(finished)) * 100, 1)

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

                dt = datetime.fromisoformat(game["gameDate"].replace("Z","+00:00"))
                time_str = dt.strftime("%H:%M")

                status = game["status"]["detailedState"]

                live = requests.get(
                    f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
                ).json()

                teams_live = live.get("liveData", {}).get("boxscore", {}).get("teams", {})

                # -------- Pitcher --------
                home_pitcher, away_pitcher = "?", "?"
                home_era, away_era = 4.2, 4.2
                home_whip, away_whip = 1.25, 1.25
                home_hand, away_hand = "R", "R"

                try:
                    hp = teams_live["home"]["pitchers"][0]
                    p = teams_live["home"]["players"][f"ID{hp}"]
                    home_pitcher = p["person"]["fullName"]
                    home_hand = p.get("pitchHand", {}).get("code","R")
                    home_era = float(p.get("stats",{}).get("pitching",{}).get("era",4.2))
                except: pass

                try:
                    ap = teams_live["away"]["pitchers"][0]
                    p = teams_live["away"]["players"][f"ID{ap}"]
                    away_pitcher = p["person"]["fullName"]
                    away_hand = p.get("pitchHand", {}).get("code","R")
                    away_era = float(p.get("stats",{}).get("pitching",{}).get("era",4.2))
                except: pass

                players = []
                has_lineup = False

                for side in ["home","away"]:
                    for p in teams_live.get(side, {}).get("players", {}).values():

                        order = p.get("battingOrder")
                        if not order: continue

                        has_lineup = True
                        lineup = int(order)//100

                        avg = p.get("stats",{}).get("batting",{}).get("avg")
                        if not avg: continue

                        try:
                            avg = float(avg)
                        except:
                            continue

                        # 👉 Gegner Pitcher
                        if side == "home":
                            prob = pro_model(avg, lineup, away_era, away_whip, away_hand)
                        else:
                            prob = pro_model(avg, lineup, home_era, home_whip, home_hand)

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
                    "away_pitcher": away_pitcher
                })

            except:
                continue

    return sorted(games, key=lambda x: x["time"])

# ---------------- WEB ----------------

@app.route("/")
def home():
    games = get_games()
    accuracy = calculate_accuracy()

    html = f"""
    <html>
    <body style="background:#0f172a;color:white;font-family:Arial">

    <h2>🔥 MLB ELITE TOOL</h2>
    <p>Trefferquote: {accuracy}%</p>
    """

    for g in games:
        html += f"<hr><b>{g['match']}</b><br>"
        html += f"{g['time']} | {g['status']}<br>"
        html += f"🏠 {g['home_pitcher']}<br>"
        html += f"✈️ {g['away_pitcher']}<br>"

        if not g["has_lineup"]:
            html += "Waiting for lineups..."
        elif not g["players"]:
            html += "No good pick"
        else:
            for p in g["players"]:
                html += f"{p['lineup']}. {p['name']} → {p['prob']}% ⭐ {p['conf']}<br>"

    html += "</body></html>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
