
from flask import Flask
import requests
import math
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
                        name = p["person"]["fullName"]

                        if name == h["name"]:
                            batting = p.get("stats", {}).get("batting", {})
                            hits = batting.get("hits", 0)

                            if hits > 0:
                                h["result"] = 1
                            else:
                                h["result"] = 0

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
    if pitcher_hand == "L":
        return avg * 1.05
    return avg

def hit_probability(avg, lineup_pos, era, whip):
    ab = estimate_ab(lineup_pos)
    base = 1 - (1 - avg) ** ab
    return base * pitcher_factor(era, whip)

def confidence(prob):
    if prob >= 0.75:
        return "ELITE 🟢"
    elif prob >= 0.68:
        return "STRONG 🟡"
    elif prob >= 0.60:
        return "SOLID 🟠"
    else:
        return "RISKY 🔴"

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
                away_pitcher_id = live["liveData"]["boxscore"]["teams"]["away"]["pitchers"][0]
                home_pitcher_id = live["liveData"]["boxscore"]["teams"]["home"]["pitchers"][0]

                away_pitcher = live["liveData"]["boxscore"]["teams"]["away"]["players"][f"ID{away_pitcher_id}"]
                home_pitcher = live["liveData"]["boxscore"]["teams"]["home"]["players"][f"ID{home_pitcher_id}"]

                away_era = float(away_pitcher["seasonStats"]["pitching"]["era"])
                home_era = float(home_pitcher["seasonStats"]["pitching"]["era"])

                away_whip = float(away_pitcher["seasonStats"]["pitching"].get("whip", 1.25))
                home_whip = float(home_pitcher["seasonStats"]["pitching"].get("whip", 1.25))

                away_hand = away_pitcher["person"]["pitchHand"]["code"]
                home_hand = home_pitcher["person"]["pitchHand"]["code"]

            except:
                away_era = home_era = 4.2
                away_whip = home_whip = 1.25
                away_hand = home_hand = "R"

            for side in ["home", "away"]:
                team = box["teams"][side]["players"]

                if side == "home":
                    era = away_era
                    whip = away_whip
                    pitcher_hand = away_hand
                else:
                    era = home_era
                    whip = home_whip
                    pitcher_hand = home_hand

                for p in team.values():
                    batting = p.get("stats", {}).get("batting", {})
                    avg = batting.get("avg")
                    order = p.get("battingOrder")

                    if avg is None:
                        continue

                    avg = float(avg)
                    avg = split_adjustment(avg, pitcher_hand)

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
        history.append({
            "name": p["name"],
            "prob": p["prob"],
            "result": None
        })

    save_history(history)

    accuracy = calculate_accuracy()

    html = f"""
    <h1>🔥 TOP 5 SAFE PICKS</h1>
    <p>Trefferquote: {accuracy}%</p>
    <button onclick="location.reload()">Refresh</button>
    """

    if not players:
        html += "<p>No strong picks today</p>"
    else:
        for p in players:
            conf = confidence(p["prob"] / 100)

            html += f"""
            <p>
            {p['name']} → {p['prob']}%<br>
            <b>{conf}</b>
            </p>
            """

    return html

# -------- START --------jo

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
