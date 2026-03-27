from flask import Flask
import requests
from datetime import datetime
import os

app = Flask(__name__)

API_KEY = "c355d24f246aa8292a71a63932649e16"

# ---------------- SAFE REQUEST ----------------

def safe_get(url):
    try:
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return {}

# ---------------- EDGE ----------------

def calc_edge(prob, odds):
    try:
        return round((prob - (1 / odds)) * 100, 2)
    except:
        return 0

# ---------------- SOCCER ----------------

def get_soccer_games():
    data = safe_get(f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey={API_KEY}&regions=eu&markets=totals")

    games = []

    for game in data:
        home = game.get("home_team")
        away = game.get("away_team")

        over_odds, under_odds = None, None

        try:
            for m in game["bookmakers"][0]["markets"]:
                if m["key"] == "totals":
                    for o in m["outcomes"]:
                        if o["name"] == "Over":
                            over_odds = o["price"]
                        elif o["name"] == "Under":
                            under_odds = o["price"]
        except:
            continue

        if not over_odds or not under_odds:
            continue

        # Simple Model
        prob_over = 0.55 if over_odds > 1.8 else 0.48
        prob_under = 1 - prob_over

        players = []

        if prob_over > (1 / over_odds):
            players.append({
                "name": "Over 2.5 Goals",
                "prob": round(prob_over * 100, 1),
                "odds": over_odds,
                "value": True
            })

        if prob_under > (1 / under_odds):
            players.append({
                "name": "Under 2.5 Goals",
                "prob": round(prob_under * 100, 1),
                "odds": under_odds,
                "value": True
            })

        games.append({
            "match": f"{away} vs {home}",
            "players": players
        })

    return games

# ---------------- TENNIS ----------------

def get_tennis_games():
    data = safe_get(f"https://api.the-odds-api.com/v4/sports/tennis_atp/odds/?apiKey={API_KEY}&regions=eu&markets=h2h")

    games = []

    for game in data:
        players = game.get("teams", [])
        if len(players) != 2:
            continue

        p1, p2 = players
        odds1, odds2 = None, None

        try:
            for o in game["bookmakers"][0]["markets"][0]["outcomes"]:
                if o["name"] == p1:
                    odds1 = o["price"]
                elif o["name"] == p2:
                    odds2 = o["price"]
        except:
            continue

        if not odds1 or not odds2:
            continue

        prob1 = (1 / odds1) * (1.05 if odds1 > 2 else 0.98)
        prob2 = (1 / odds2) * (1.05 if odds2 > 2 else 0.98)

        players_out = []

        if prob1 > (1 / odds1):
            players_out.append({
                "name": p1,
                "prob": round(prob1 * 100, 1),
                "odds": odds1,
                "value": True
            })

        if prob2 > (1 / odds2):
            players_out.append({
                "name": p2,
                "prob": round(prob2 * 100, 1),
                "odds": odds2,
                "value": True
            })

        games.append({
            "match": f"{p1} vs {p2}",
            "players": players_out
        })

    return games

# ---------------- SHARP MODE ----------------

def get_all_picks():
    all_picks = []

    # Soccer
    try:
        for g in get_soccer_games():
            for p in g["players"]:
                edge = calc_edge(p["prob"]/100, p["odds"])
                all_picks.append({
                    "sport": "⚽",
                    "match": g["match"],
                    "name": p["name"],
                    "prob": p["prob"],
                    "odds": p["odds"],
                    "edge": edge
                })
    except:
        pass

    # Tennis
    try:
        for g in get_tennis_games():
            for p in g["players"]:
                edge = calc_edge(p["prob"]/100, p["odds"])
                all_picks.append({
                    "sport": "🎾",
                    "match": g["match"],
                    "name": p["name"],
                    "prob": p["prob"],
                    "odds": p["odds"],
                    "edge": edge
                })
    except:
        pass

    return sorted(all_picks, key=lambda x: x["edge"], reverse=True)

# ---------------- WEB ----------------

@app.route("/")
def home():
    picks = get_all_picks()

    html = """
    <html>
    <body style='background:#0f172a;color:white;font-family:Arial'>
    <h1 style='text-align:center'>🔥 SHARP MODE</h1>
    """

    if not picks:
        html += "<p style='padding:20px'>⏳ Keine Value Bets aktuell</p>"

    for p in picks[:10]:
        html += f"""
        <div style='background:#1e293b;margin:10px;padding:10px;border-radius:10px'>
        {p['sport']}<br>
        {p['match']}<br>
        ⭐ {p['name']}<br>
        {p['prob']}% | {p['odds']}<br>
        🔥 Edge: {p['edge']}%
        </div>
        """

    html += "</body></html>"
    return html

# ---------------- RUN ----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
