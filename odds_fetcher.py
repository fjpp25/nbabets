"""
odds_fetcher.py
Fetches today's NBA games and bookmaker odds from The Odds API.
Supports three markets: h2h (moneyline), spreads, and totals (over/under).
"""

import requests
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────
SPORT_KEY   = "basketball_nba"
REGIONS     = "eu"
MARKETS     = "h2h,spreads,totals"   # fetch all three in one request
ODDS_FORMAT = "decimal"
BASE_URL    = "https://api.the-odds-api.com/v4"

# NBA games run on ET — filter by ET date so European users don't miss games
# that tip off after midnight UTC
ET_OFFSET = timedelta(hours=-5)


# ── Main fetcher ──────────────────────────────────────────────────────────────
def fetch_todays_games(api_key: str, bookmakers: Optional[list[str]] = None) -> list[dict]:
    """
    Returns a list of today's NBA games with odds for all three markets.

    Each game dict:
    {
        "id":            "...",
        "home_team":     "Boston Celtics",
        "away_team":     "Miami Heat",
        "commence_time": "2025-01-15T00:10:00Z",
        "bookmakers": [
            {
                "key":   "unibet_fr",
                "title": "Unibet (FR)",
                "markets": {
                    "h2h": {
                        "Boston Celtics": 1.65,
                        "Miami Heat":     2.30
                    },
                    "spreads": {
                        "Boston Celtics": {"line": -5.5, "odds": 1.91},
                        "Miami Heat":     {"line": +5.5, "odds": 1.91}
                    },
                    "totals": {
                        "Over":  {"line": 221.5, "odds": 1.91},
                        "Under": {"line": 221.5, "odds": 1.91}
                    }
                }
            },
            ...
        ]
    }
    """
    url    = f"{BASE_URL}/sports/{SPORT_KEY}/odds"
    params = {
        "apiKey":     api_key,
        "regions":    REGIONS,
        "markets":    MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": "iso",
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    raw_games = response.json()
    today_et  = (datetime.now(timezone.utc) + ET_OFFSET).date()
    games     = []

    for game in raw_games:
        commence    = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        commence_et = commence + ET_OFFSET
        if commence_et.date() != today_et:
            continue
        games.append(_parse_game(game))

    _log_quota(response)
    return games


def fetch_available_bookmakers(api_key: str) -> list[dict]:
    """Lists all EU bookmakers available for NBA."""
    url    = f"{BASE_URL}/sports/{SPORT_KEY}/odds"
    params = {
        "apiKey":     api_key,
        "regions":    REGIONS,
        "markets":    "h2h",
        "oddsFormat": ODDS_FORMAT,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    seen = {}
    for game in response.json():
        for bm in game.get("bookmakers", []):
            seen[bm["key"]] = bm["title"]

    _log_quota(response)
    return [{"key": k, "title": v} for k, v in sorted(seen.items())]


# ── Parsing ───────────────────────────────────────────────────────────────────
def _parse_game(raw: dict) -> dict:
    """Normalises a raw API game object into a clean unified structure."""
    bookmakers = []
    for bm in raw.get("bookmakers", []):
        markets = {}
        for market in bm.get("markets", []):
            key = market["key"]
            if key == "h2h":
                markets["h2h"] = {
                    o["name"]: o["price"]
                    for o in market["outcomes"]
                }
            elif key == "spreads":
                markets["spreads"] = {
                    o["name"]: {"line": o["point"], "odds": o["price"]}
                    for o in market["outcomes"]
                }
            elif key == "totals":
                markets["totals"] = {
                    o["name"]: {"line": o["point"], "odds": o["price"]}
                    for o in market["outcomes"]
                }
        if markets:
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


# ── Market accessors ──────────────────────────────────────────────────────────
def best_h2h_odds(game: dict, team: str) -> tuple[Optional[float], str]:
    """Returns (best decimal odds, bookmaker title) for a team's moneyline."""
    best, book = None, ""
    for bm in game["bookmakers"]:
        h2h = bm["markets"].get("h2h", {})
        for t, odds in h2h.items():
            if _names_match(t, team) and (best is None or odds > best):
                best, book = odds, bm["title"]
    return best, book


def consensus_spread(game: dict, team: str) -> Optional[float]:
    """
    Returns the consensus spread line for a team (average across bookmakers).
    Negative = favourite, positive = underdog.
    """
    lines = []
    for bm in game["bookmakers"]:
        spreads = bm["markets"].get("spreads", {})
        for t, data in spreads.items():
            if _names_match(t, team):
                lines.append(data["line"])
    return round(sum(lines) / len(lines), 1) if lines else None


def best_spread_odds(game: dict, team: str) -> tuple[Optional[float], Optional[float], str]:
    """Returns (best odds, line, bookmaker) for a team's spread bet."""
    best_odds, best_line, book = None, None, ""
    for bm in game["bookmakers"]:
        spreads = bm["markets"].get("spreads", {})
        for t, data in spreads.items():
            if _names_match(t, team):
                if best_odds is None or data["odds"] > best_odds:
                    best_odds, best_line, book = data["odds"], data["line"], bm["title"]
    return best_odds, best_line, book


def consensus_total(game: dict) -> Optional[float]:
    """Returns the consensus over/under line (average across bookmakers)."""
    lines = []
    for bm in game["bookmakers"]:
        totals = bm["markets"].get("totals", {})
        if "Over" in totals:
            lines.append(totals["Over"]["line"])
    return round(sum(lines) / len(lines), 1) if lines else None


def best_total_odds(game: dict, side: str) -> tuple[Optional[float], Optional[float], str]:
    """
    Returns (best odds, line, bookmaker) for 'Over' or 'Under'.
    side should be 'Over' or 'Under'.
    """
    best_odds, best_line, book = None, None, ""
    for bm in game["bookmakers"]:
        totals = bm["markets"].get("totals", {})
        if side in totals:
            data = totals[side]
            if best_odds is None or data["odds"] > best_odds:
                best_odds, best_line, book = data["odds"], data["line"], bm["title"]
    return best_odds, best_line, book


def implied_probability(decimal_odds: float) -> float:
    """Converts decimal odds to implied probability (0–1)."""
    return 1 / decimal_odds



PINNACLE_KEY = "pinnacle"


def pinnacle_h2h_odds(game: dict, team: str) -> tuple[Optional[float], Optional[float]]:
    """
    Returns (home_odds, away_odds) from Pinnacle specifically.
    Falls back to (None, None) if Pinnacle isn't available for this game.
    Used as the reference for H2H edge calculation.
    """
    for bm in game["bookmakers"]:
        if bm["key"] != PINNACLE_KEY:
            continue
        h2h = bm["markets"].get("h2h", {})
        home_odds = next((o for t, o in h2h.items()
                          if _names_match(t, game["home_team"])), None)
        away_odds = next((o for t, o in h2h.items()
                          if _names_match(t, game["away_team"])), None)
        return home_odds, away_odds
    return None, None


def pinnacle_spread(game: dict, team: str) -> Optional[float]:
    """
    Returns Pinnacle's spread line for a team.
    Falls back to consensus if Pinnacle unavailable.
    """
    for bm in game["bookmakers"]:
        if bm["key"] != PINNACLE_KEY:
            continue
        spreads = bm["markets"].get("spreads", {})
        for t, data in spreads.items():
            if _names_match(t, team):
                return data["line"]
    # fallback to consensus
    return consensus_spread(game, team)


def pinnacle_total(game: dict) -> Optional[float]:
    """
    Returns Pinnacle's over/under line.
    Falls back to consensus if Pinnacle unavailable.
    """
    for bm in game["bookmakers"]:
        if bm["key"] != PINNACLE_KEY:
            continue
        totals = bm["markets"].get("totals", {})
        if "Over" in totals:
            return totals["Over"]["line"]
    # fallback to consensus
    return consensus_total(game)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _names_match(a: str, b: str) -> bool:
    a_words = {w.lower() for w in a.split() if len(w) > 3}
    b_words = {w.lower() for w in b.split() if len(w) > 3}
    return bool(a_words & b_words)


def _log_quota(response: requests.Response) -> None:
    remaining = response.headers.get("x-requests-remaining", "?")
    used      = response.headers.get("x-requests-used", "?")
    print(f"[Odds API] Requests used: {used} | Remaining: {remaining}")


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise ValueError("ODDS_API_KEY not set in .env file")

    print("=== Today's NBA Games — All Markets ===\n")
    games = fetch_todays_games(api_key)
    if not games:
        print("No NBA games today.")

    for g in games:
        print(f"  {g['away_team']} @ {g['home_team']}  ({g['commence_time']})")

        # h2h
        for bm in g["bookmakers"][:2]:   # show first 2 books for brevity
            h2h = bm["markets"].get("h2h", {})
            if h2h:
                odds_str = "  |  ".join(f"{t}: {o}" for t, o in h2h.items())
                print(f"    [H2H]     [{bm['title']}]  {odds_str}")

        # spread
        spread_line = consensus_spread(g, g["home_team"])
        if spread_line is not None:
            print(f"    [SPREAD]  Consensus line — "
                  f"{g['home_team']}: {spread_line:+.1f}  |  "
                  f"{g['away_team']}: {-spread_line:+.1f}")

        # totals
        total_line = consensus_total(g)
        if total_line is not None:
            o_odds, _, o_book = best_total_odds(g, "Over")
            u_odds, _, u_book = best_total_odds(g, "Under")
            print(f"    [TOTAL]   Line: {total_line}  |  "
                  f"Best Over: {o_odds} ({o_book})  |  "
                  f"Best Under: {u_odds} ({u_book})")
        print()
