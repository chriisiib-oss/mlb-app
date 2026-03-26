
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

# -------- DATA --------

def get_players():
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
    data = requests.get(url).json()

    players = []

    for date in data.get("dates", []):
        for game in date.get("games", []):

            game_id = game["gamePk"]

            box = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore").json()
            live = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_id}/feed/live").json()

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

                    prob = hit_probability(avg, lineup_pos, era, whip)

                    if prob >= 0.60 and lineup_pos <= 5:
                        players.append({
                            "name": p["person"]["fullName"],
                            "prob": round(prob * 100, 1)
                        })

    return sorted(players, key=lambda x: x["prob"], reverse=True)[:5]

# -------- WEB --------

@app.route("/")
def home():
    update_results()
    players = get_players()

    history = load_history()
    for p in players:
        history.append({"name": p["name"], "prob": p["prob"], "result": None})
    save_history(history)

    accuracy = calculate_accuracy()

    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{
    margin:0;
    font-family:-apple-system;
    background:#0f172a;
    color:white;
}}

.header {{
    background:linear-gradient(135deg,#22c55e,#16a34a);
    padding:25px;
    text-align:center;
    font-size:24px;
    font-weight:bold;
}}

.top {{
    background:#1e293b;
    margin:15px;
    padding:20px;
    border-radius:20px;
    text-align:center;
}}

.container {{
    padding:15px;
}}

.card {{
    background:#1e293b;
    border-radius:18px;
    padding:18px;
    margin-bottom:15px;
}}

.bar {{
    height:8px;
    background:#334155;
    border-radius:10px;
    overflow:hidden;
}}

.fill {{
    height:100%;
}}

.green {{background:#22c55e}}
.yellow {{background:#eab308}}
.orange {{background:#f97316}}
.red {{background:#ef4444}}

button {{
    width:90%;
    margin:20px auto;
    display:block;
    padding:14px;
    border-radius:14px;
    border:none;
    background:#22c55e;
    font-weight:bold;
}}
</style>
</head>

<body>

<div class="header">🔥 MLB PICKS</div>

<div class="top">
Trefferquote: {accuracy}%
</div>

<button onclick="location.reload()">Refresh</button>

<div class="container">
"""

    for i, p in enumerate(players):
        prob = p["prob"]
        conf = confidence(prob/100)

        color = "green" if prob>75 else "yellow" if prob>68 else "orange" if prob>60 else "red"

        html += f"""
        <div class="card">
        <b>{p['name']}</b><br>
        {prob}%<br>
        <div class="bar"><div class="fill {color}" style="width:{prob}%"></div></div>
        {conf}
        </div>
        """

    html += "</div></body></html>"

    return html

# -------- START --------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
