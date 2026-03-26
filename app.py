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

# -------- DATA --------

def get_games():
    games = []
    now = datetime.now(timezone.utc)

    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    all_games = []

    for d in [today, tomorrow]:
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d}"
        data = requests.get(url, timeout=5).json()

        for date in data.get("dates", []):
            all_games.extend(date.get("games", []))

    for game in all_games:
        game_time = game.get("gameDate")
        if not game_time:
            continue

        dt = datetime.fromisoformat(game_time.replace("Z", "+00:00"))

        diff = (dt - now).total_seconds()

        # 👉 24h Fenster
        if diff < -12 * 3600 or diff > 12 * 3600:
            continue

        teams = game.get("teams", {})

        home_team = teams.get("home", {}).get("team", {}).get("name", "Home")
        away_team = teams.get("away", {}).get("team", {}).get("name", "Away")

        status = game.get("status", {}).get("detailedState", "Unknown")
        time_str = dt.strftime("%H:%M")

        games.append({
            "match": f"{away_team} vs {home_team}",
            "time": time_str,
            "status": status
        })

    return sorted(games, key=lambda x: x["time"])

# -------- WEB --------

@app.route("/")
def home():
    try:
        games = get_games()

        html = "<h1>DEBUG MLB APP</h1>"

        for g in games:
            html += f"<p>{g['match']} - {g['time']} - {g['status']}</p>"

        return html

    except Exception as e:
        return f"""
        <h1 style='color:red'>ERROR</h1>
        <pre>{str(e)}</pre>
        """

# -------- START --------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
