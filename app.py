from flask import Flask
import requests
import json
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

history_file = "history.json"

# -------- HISTORY --------

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

# -------- AUTO LEARNING --------

def model_adjustment():
    history = load_history()
    finished = [h for h in history if h["result"] is not None]

    if len(finished) < 20:
        return 1.0

    avg_pred = sum(h["prob"] for h in finished) / len(finished) / 100
    real = sum(h["result"] for h in finished) / len(finished)

    return real / avg_pred if avg_pred > 0 else 1.0

def player_winrate(name):
    history = load_history()
    data = [h for h in history if h["name"] == name and h["result"] is not None]

    if len(data) < 5:
        return None

    wins = sum(1 for d in data if d["result"] == 1)
    return wins / len(data)

# -------- MODEL --------

def pro_model(avg, lineup_pos, era, whip):
    ab = 4 if lineup_pos <= 5 else 3
    base = 1 - (1 - avg) ** ab

    pitcher_adj = 1.0
    if era > 4.5:
        pitcher_adj += 0.18
    elif era < 3.2:
        pitcher_adj -= 0.18

    if whip > 1.35:
        pitcher_adj += 0.10
    elif whip < 1.05:
        pitcher_adj -= 0.10

    batter_adj = 1.0
    if avg > 0.300:
        batter_adj += 0.12
    elif avg < 0.220:
        batter_adj -= 0.12

    prob = base * pitcher_adj * batter_adj
    return max(0.05, min(prob, 0.95))

def pro_confidence(prob, avg, lineup_pos):
    score = prob * 10
    if avg > 0.300:
        score += 1.2
    if lineup_pos <= 3:
        score += 1
    if prob > 0.70:
        score += 1
    if prob > 0.80:
        score += 1
    return round(score, 1)

def is_lock(prob, conf):
    return prob >= 0.65 and conf >= 8

def calculate_value(prob, odds):
    implied = 1 / odds
    value = prob - implied
    return round(value * 100, 1)

# -------- RESULTS --------

def update_results():
    history = load_history()
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
    data = requests.get(url).json()

    for h in history:
        if h["result"] is not None:
            continue

        for date in data.get("dates", []):
            for game in date.get("games", []):
                try:
                    game_id = game["gamePk"]
                    box = requests.get(
                        f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
                    ).json()

                    for side in ["home", "away"]:
                        players = box.get("teams", {}).get(side, {}).get("players", {})
                        for p in players.values():
                            if p["person"]["fullName"] == h["name"]:
                                hits = p.get("stats", {}).get("batting", {}).get("hits", 0)
                                h["result"] = 1 if hits > 0 else 0
                except:
                    continue

    save_history(history)

# -------- DATA --------

def get_games():
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
    data = requests.get(url).json()

    games = []
    adj = model_adjustment()

    odds_data = {
        "Aaron Judge": 1.80,
        "Juan Soto": 1.75
    }

    for date in data.get("dates", []):
        for game in date.get("games", []):

            try:
                dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))

                game_id = game["gamePk"]
                teams = game["teams"]

                home = teams["home"]["team"]["name"]
                away = teams["away"]["team"]["name"]

                status = game["status"]["detailedState"]
                time_str = dt.strftime("%H:%M")

                box = requests.get(
                    f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
                ).json()

                teams_data = box.get("teams", {})

                players_list = []
                has_lineup = False

                pitcher_era = 4.2
                pitcher_whip = 1.25

                for side in ["home", "away"]:
                    team = teams_data.get(side, {}).get("players", {})

                    for p in team.values():
                        avg = p.get("stats", {}).get("batting", {}).get("avg")
                        order = p.get("battingOrder")

                        if avg is None:
                            continue

                        avg = float(avg)

                        if order:
                            has_lineup = True

                        lineup_pos = int(order)//100 if order else 5

                        prob = pro_model(avg, lineup_pos, pitcher_era, pitcher_whip)
                        prob *= adj

                        winrate = player_winrate(p["person"]["fullName"])
                        if winrate:
                            if winrate > 0.65:
                                prob *= 1.05
                            elif winrate < 0.45:
                                prob *= 0.95

                        prob = max(0.05, min(prob, 0.95))

                        conf = pro_confidence(prob, avg, lineup_pos)

                        name = p["person"]["fullName"]
                        odds = odds_data.get(name)
                        value = calculate_value(prob, odds) if odds else None

                        if prob >= 0.55:
                            players_list.append({
                                "name": name,
                                "prob": round(prob * 100, 1),
                                "conf": conf,
                                "value": value,
                                "lock": is_lock(prob, conf)
                            })

                players_list = sorted(players_list, key=lambda x: x["conf"], reverse=True)[:3]

                games.append({
                    "match": f"{away} vs {home}",
                    "time": time_str,
                    "status": status,
                    "has_lineup": has_lineup,
                    "players": players_list
                })

            except:
                continue

    return sorted(games, key=lambda x: x["time"])

# -------- WEB --------

@app.route("/")
def home():
    update_results()

    games = get_games()

    all_players = [p for g in games for p in g["players"]]
    top_players = sorted(all_players, key=lambda x: x["conf"], reverse=True)[:5]
    locks = [p for p in all_players if p["lock"]]

    accuracy = calculate_accuracy()

    html = f"""
    <html>
    <body style="background:#0f172a;color:white;font-family:sans-serif">
    <h2>🔥 MLB PRO TOOL</h2>
    <p>📊 Trefferquote: {accuracy}%</p>
    """

    html += "<h3>🔒 LOCK PICKS</h3>"
    if not locks:
        html += "<p>No safe picks</p>"
    else:
        for p in locks:
            html += f"<p>🔒 {p['name']} → {p['prob']}% (⭐ {p['conf']})</p>"

    html += "<h3>🔥 TOP 5 PICKS</h3>"
    for p in top_players:
        html += f"<p>{p['name']} → {p['prob']}% (⭐ {p['conf']})</p>"

    html += "<hr><h3>⚾ ALL GAMES</h3>"

    for g in games:
        html += f"<p><b>{g['match']}</b><br>{g['time']} | {g['status']}<br>"

        if not g["has_lineup"]:
            html += "Waiting for lineups..."
        elif not g["players"]:
            html += "No good pick"
        else:
            for p in g["players"]:
                val = f" | VALUE +{p['value']}%" if p["value"] else ""
                html += f"{p['name']} → {p['prob']}% (⭐ {p['conf']}){val}<br>"

        html += "</p>"

    html += "</body></html>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
