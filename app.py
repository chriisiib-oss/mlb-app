
from flask import Flask
import requests
import json
import os

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

    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
    data = requests.get(url).json()

    for h in history:
        if h["result"] is not None:
            continue

        for date in data.get("dates", []):
            for game in date.get("games", []):

                game_id = game["gamePk"]
                box = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore").json()

                for side in ["home", "away"]:
                    players = box["teams"][side]["players"]

                    for p in players.values():
                        if p["person"]["fullName"] == h["name"]:
                            hits = p.get("stats", {}).get("batting", {}).get("hits", 0)
                            h["result"] = 1 if hits > 0 else 0

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

def split_adjustment(avg, pitcher_hand):
    return avg * 1.05 if pitcher_hand == "L" else avg

def hit_probability(avg, lineup_pos, era, whip):
    base = 1 - (1 - avg) ** estimate_ab(lineup_pos)
    return base * pitcher_factor(era, whip)

def confidence(prob):
    if prob >= 0.75:
        return "ELITE"
    elif prob >= 0.68:
        return "STRONG"
    elif prob >= 0.60:
        return "SOLID"
    return "RISKY"

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
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
    data = requests.get(url).json()

    games = []
    adj = model_adjustment()

    for date in data.get("dates", []):
        for game in date.get("games", []):

            game_id = game["gamePk"]
            teams = game["teams"]

            home_team = teams["home"]["team"]["name"]
            away_team = teams["away"]["team"]["name"]

            box = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore").json()
            live = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_id}/feed/live").json()

            players_list = []

            try:
                away_id = live["liveData"]["boxscore"]["teams"]["away"]["pitchers"][0]
                home_id = live["liveData"]["boxscore"]["teams"]["home"]["pitchers"][0]

                away = live["liveData"]["boxscore"]["teams"]["away"]["players"][f"ID{away_id}"]
                home = live["liveData"]["boxscore"]["teams"]["home"]["players"][f"ID{home_id}"]

                away_era = float(away["seasonStats"]["pitching"]["era"])
                home_era = float(home["seasonStats"]["pitching"]["era"])

                away_whip = float(away["seasonStats"]["pitching"].get("whip", 1.25))
                home_whip = float(home["seasonStats"]["pitching"].get("whip", 1.25))

                away_hand = away["person"]["pitchHand"]["code"]
                home_hand = home["person"]["pitchHand"]["code"]

            except:
                away_era = home_era = 4.2
                away_whip = home_whip = 1.25
                away_hand = home_hand = "R"

            for side in ["home", "away"]:
                team = box["teams"][side]["players"]

                era = away_era if side == "home" else home_era
                whip = away_whip if side == "home" else home_whip
                hand = away_hand if side == "home" else home_hand

                for p in team.values():
                    avg = p.get("stats", {}).get("batting", {}).get("avg")
                    order = p.get("battingOrder")

                    if avg is None:
                        continue

                    avg = split_adjustment(float(avg), hand)
                    lineup_pos = int(order)//100 if order else 5

                    prob = hit_probability(avg, lineup_pos, era, whip) * adj
                    prob = max(0.05, min(prob, 0.95))

                    if prob >= 0.55:
                        players_list.append({
                            "name": p["person"]["fullName"],
                            "prob": round(prob * 100, 1)
                        })

            players_list = sorted(players_list, key=lambda x: x["prob"], reverse=True)[:5]

            if players_list:
                games.append({
                    "match": f"{away_team} vs {home_team}",
                    "players": players_list
                })

    return games

# -------- WEB --------

@app.route("/")
def home():
    update_results()
    games = get_games()

    all_players = []
    for g in games:
        for p in g["players"]:
            all_players.append(p)

    all_players = sorted(all_players, key=lambda x: x["prob"], reverse=True)

    top_player = all_players[0] if all_players else None

    locks = []
    for p in all_players:
        winrate = player_winrate(p["name"])

        if p["prob"] >= 65:
            if winrate is None or winrate >= 0.6:
                locks.append({
                    "name": p["name"],
                    "prob": p["prob"],
                    "winrate": winrate
                })

    locks = locks[:5]
    accuracy = calculate_accuracy()

    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{background:#0f172a;color:white;font-family:-apple-system;margin:0}}
.header {{background:linear-gradient(135deg,#22c55e,#16a34a);padding:20px;text-align:center;font-size:22px}}
.lock {{background:#22c55e;color:black;margin:15px;padding:20px;border-radius:20px;text-align:center}}
.container {{padding:15px}}
.card {{background:#1e293b;padding:15px;margin-bottom:10px;border-radius:15px}}
</style>
</head>

<body>

<div class="header">🔥 WIN MODE</div>

<div class="lock">
Trefferquote: {accuracy}%
</div>
"""

    if top_player:
        html += f"<div class='lock'>🏆 {top_player['name']} → {top_player['prob']}%</div>"

    html += "<div class='container'>"

    for p in locks:
        win = f"{round(p['winrate']*100,1)}%" if p["winrate"] else "New"

        html += f"""
        <div class="card">
        {p['name']} → {p['prob']}%<br>
        Winrate: {win}
        </div>
        """

    html += "</div></body></html>"

    return html

# -------- START --------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
