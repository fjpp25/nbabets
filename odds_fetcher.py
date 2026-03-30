"""
odds_fetcher.py
Fetches today's NBA games and bookmaker odds from The Odds API.
"""

import requests
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── Constants ────────────────────────────────────────────────────────────────
SPORT_KEY = "basketball_nba"
REGIONS   = "eu"          # eu | us | uk | au  — use 'eu' for Betclic-style books
MARKETS   = "h2h"         # h2h = moneyline; add 'spreads,totals' later
ODDS_FORMAT = "decimal"   # decimal suits European users; use 'american' if preferred
BASE_URL  = "https://api.the-odds-api.com/v4"


# ── Main fetcher ──────────────────────────────────────────────────────────────
def fetch_todays_games(api_key: str, bookmakers: Optional[list[str]] = None) -> list[dict]:
    """
    Returns a list of today's NBA games with odds.

    Each game dict looks like:
    {
        "id":           "...",
        "home_team":    "Boston Celtics",
        "away_team":    "Miami Heat",
        "commence_time": "2025-01-15T00:10:00Z",
        "bookmakers": [
            {
                "key":   "unibet_eu",
                "title": "Unibet",
                "markets": {
                    "h2h": {
                        "Boston Celtics": 1.65,
                        "Miami Heat":     2.30
                    }
                }
            },
            ...
        ]
    }
    """
    url = f"{BASE_URL}/sports/{SPORT_KEY}/odds"
    params = {
        "apiKey":      api_key,
        "regions":     REGIONS,
        "markets":     MARKETS,
        "oddsFormat":  ODDS_FORMAT,
        "dateFormat":  "iso",
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    raw_games = response.json()
    # NBA games tip off in US Eastern time (UTC-4 in summer, UTC-5 in winter).
    # We filter by ET date so European users don't miss games that fall on
    # the next UTC day. UTC-5 is a safe conservative offset year-round.
    ET_OFFSET  = timedelta(hours=-5)
    et_now     = datetime.now(timezone.utc) + ET_OFFSET
    today_et   = et_now.date()
    games      = []

    for game in raw_games:
        commence    = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        commence_et = commence + ET_OFFSET
        if commence_et.date() != today_et:
            continue  # skip games not on today's ET date

        games.append(_parse_game(game))

    _log_quota(response)
    return games


def fetch_available_bookmakers(api_key: str) -> list[dict]:
    """Lists all bookmakers available under the 'eu' region for NBA — useful for setup."""
    url = f"{BASE_URL}/sports/{SPORT_KEY}/odds"
    params = {
        "apiKey":     api_key,
        "regions":    REGIONS,
        "markets":    MARKETS,
        "oddsFormat": ODDS_FORMAT,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    seen  = {}
    for game in response.json():
        for bm in game.get("bookmakers", []):
            seen[bm["key"]] = bm["title"]

    _log_quota(response)
    return [{"key": k, "title": v} for k, v in sorted(seen.items())]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _parse_game(raw: dict) -> dict:
    """Normalises a raw API game object into a clean structure."""
    bookmakers = []
    for bm in raw.get("bookmakers", []):
        markets = {}
        for market in bm.get("markets", []):
            if market["key"] == "h2h":
                markets["h2h"] = {
                    outcome["name"]: outcome["price"]
                    for outcome in market["outcomes"]
                }
        bookmakers.append({
            "key":     bm["key"],
            "title":   bm["title"],
            "markets": markets,
        })

    return {
        "id":            raw["id"],
        "home_team":     raw["home_team"],
        "away_team":     raw["away_team"],
        "commence_time": raw["commence_time"],
        "bookmakers":    bookmakers,
    }


def _log_quota(response: requests.Response) -> None:
    """Prints remaining API quota from response headers."""
    remaining = response.headers.get("x-requests-remaining", "?")
    used      = response.headers.get("x-requests-used", "?")
    print(f"[Odds API] Requests used: {used} | Remaining: {remaining}")


def best_odds(game: dict, team: str) -> Optional[float]:
    """Returns the best (highest) decimal odds available for a given team across all bookmakers."""
    odds_list = []
    for bm in game["bookmakers"]:
        h2h = bm["markets"].get("h2h", {})
        if team in h2h:
            odds_list.append(h2h[team])
    return max(odds_list) if odds_list else None


def implied_probability(decimal_odds: float) -> float:
    """Converts decimal odds to implied probability (0–1)."""
    return 1 / decimal_odds


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise ValueError("ODDS_API_KEY not set in .env file")

    print("=== Available EU bookmakers for NBA ===")
    books = fetch_available_bookmakers(api_key)
    for b in books:
        print(f"  {b['key']:30s} {b['title']}")

    print("\n=== Today's NBA Games ===")
    games = fetch_todays_games(api_key)
    if not games:
        print("No NBA games today (or all games are already finished).")
    for g in games:
        print(f"\n  {g['away_team']} @ {g['home_team']}  ({g['commence_time']})")
        for bm in g["bookmakers"]:
            h2h = bm["markets"].get("h2h", {})
            odds_str = "  |  ".join(f"{t}: {o}" for t, o in h2h.items())
            print(f"    [{bm['title']}]  {odds_str}")
