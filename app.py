from flask import Flask
import requests
import json
import os
from datetime import datetime, timedelta

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

# -------- AUTO RESULTS --------

def update_results():
    history = load_history()

    us_today = (datetime.utcnow() - timedelta(hours=4)).strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={us_today}"

    data = requests.get(url).json()

    for h in history:
        if h["result"] is not None:
            continue

        for date in data.get("dates", []):
            for game in date.get("games", []):

                try:
                    game_id = game["gamePk"]
                    box = requests.get(
                        f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore",
                        timeout=5
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

# -------- MODEL --------

def estimate_ab(lineup_pos):
    return 4 if lineup_pos <= 5 else 3

def pitcher_factor(era, whip):
    factor = 1.0
    if era > 4.5:
        factor += 0.12
    elif era < 3.5:
        factor -= 0.12
    if whip > 1.3:
        factor += 0.06
    elif whip < 1.1:
        factor -= 0.06
    return factor

def hit_probability(avg, lineup_pos, era, whip):
    base = 1 - (1 - avg) ** estimate_ab(lineup_pos)
    return base * pitcher_factor(era, whip)

# -------- AUTO OPTIMIZATION --------

def model_adjustment():
    history = load_history()
    finished = [h for h in history if h["result"] is not None]

    if len(finished) < 10:
        return 1.0

    avg_prob = sum(h["prob"] for h in finished) / len(finished) / 100
    real = sum(h["result"] for h in finished) / len(finished)

    return real / avg_prob if avg_prob > 0 else 1.0

def player_winrate(name):
    history = load_history()
    data = [h for h in history if h["name"] == name and h["result"] is not None]

    if len(data) < 3:
        return None

    wins = sum(1 for d in data if d["result"] == 1)
    return wins / len(data)

# -------- DATA --------

def get_games():
    # 🔥 FIX: richtiger MLB Spieltag
    us_today = (datetime.utcnow() - timedelta(hours=4)).strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={us_today}"

    data = requests.get(url).json()

    games = []
    adj = model_adjustment()

    dates = data.get("dates", [])

    if not dates:
        return []

    date = dates[0]

    for game in date.get("games", []):

        try:
            game_id = game["gamePk"]
            teams = game["teams"]

            home_team = teams["home"]["team"]["name"]
            away_team = teams["away"]["team"]["name"]

            status = game["status"]["detailedState"]

            dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
            time_str = dt.strftime("%H:%M")

            box = requests.get(
                f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore",
                timeout=5
            ).json()

            teams_data = box.get("teams", {})

            players_list = []
            has_lineup = False

            for side in ["home", "away"]:
                team = teams_data.get(side, {}).get("players", {})

                if not team:
                    continue

                for p in team.values():
                    avg = p.get("stats", {}).get("batting", {}).get("avg")
                    order = p.get("battingOrder")

                    if avg is None:
                        continue

                    avg = float(avg)

                    if order:
                        has_lineup = True

                    lineup_pos = int(order)//100 if order else 5

                    if has_lineup:
                        prob = hit_probability(avg, lineup_pos, 4.2, 1.25) * adj
                    else:
                        prob = avg * 3.5

                    prob = max(0.05, min(prob, 0.95))

                    if (has_lineup and prob >= 0.60) or (not has_lineup and prob >= 0.50):
                        players_list.append({
                            "name": p["person"]["fullName"],
                            "prob": round(prob * 100, 1)
                        })

            players_list = sorted(players_list, key=lambda x: x["prob"], reverse=True)[:3]

            games.append({
                "match": f"{away_team} vs {home_team}",
                "players": players_list,
                "has_lineup": has_lineup,
                "time": time_str,
                "status": status
            })

        except:
            continue

    return sorted(games, key=lambda x: x["time"])

def get_best_game(games):
    best = None
    best_score = 0

    for g in games:
        if not g["players"]:
            continue

        score = g["players"][0]["prob"] + len(g["players"]) * 2

        if score > best_score:
            best_score = score
            best = g

    return best

# -------- WEB --------

@app.route("/")
def home():
    update_results()

    games = get_games()
    best_game = get_best_game(games)

    all_players = [p for g in games for p in g["players"]]
    all_players = sorted(all_players, key=lambda x: x["prob"], reverse=True)

    adj = model_adjustment()

    locks = []
    for p in all_players:
        winrate = player_winrate(p["name"])

        if p["prob"] >= 65:
            if winrate is not None and winrate >= 0.6:
                if 0.9 <= adj <= 1.1:
                    locks.append({
                        "name": p["name"],
                        "prob": p["prob"],
                        "winrate": winrate
                    })

    accuracy = calculate_accuracy()

    html = f"""
    <html>
    <body style="background:#0f172a;color:white;font-family:sans-serif">

    <h2>🔥 LIVE MLB APP</h2>
    <p>Trefferquote: {accuracy}%</p>
    """

    if best_game:
        html += f"<h3>🏆 BEST GAME: {best_game['match']}</h3>"

    if not locks:
        html += "<h3>❌ NO BET TODAY</h3>"
    else:
        html += "<h3>🔥 ONLY BET PICKS</h3>"
        for p in locks:
            win = round(p["winrate"] * 100, 1)
            html += f"<p>{p['name']} → {p['prob']}% | Winrate {win}%</p>"

    html += "<hr><h3>⚾ ALL GAMES</h3>"

    for g in games:

        if "Live" in g["status"]:
            status = "🔴 LIVE"
        elif "Final" in g["status"]:
            status = "⚫ Final"
        else:
            status = "🟡 Upcoming"

        html += f"<p><b>{g['match']}</b><br>{g['time']} | {status}<br>"

        if not g["has_lineup"]:
            html += "Waiting for lineups..."
        elif not g["players"]:
            html += "No good pick"
        else:
            for p in g["players"]:
                html += f"{p['name']} → {p['prob']}%<br>"

        html += "</p>"

    html += "</body></html>"

    return html

# -------- START --------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
