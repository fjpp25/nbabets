"""
nba_stats.py
Fetches team performance data exclusively from balldontlie.io.

Derived stats (all computed from raw game results):
  - season_win_pct      overall W/L ratio over last LOOKBACK_DAYS days
  - last_10_win_pct     win% over the last 10 games (hot/cold indicator)
  - home_win_pct        win% when playing at home
  - away_win_pct        win% when playing away
  - avg_point_diff      average margin of victory/defeat (proxy for net rating)
  - avg_points_for      average points scored per game
  - avg_points_against  average points conceded per game
  - recent_form         list of last 5 results [1=win, 0=loss], newest first
  - rest_days           days since last game
"""

import requests
from datetime import datetime, timezone, timedelta


# ── Constants ─────────────────────────────────────────────────────────────────
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"
LOOKBACK_DAYS    = 90   # how far back to search for recent games


# ── Public interface ──────────────────────────────────────────────────────────
def get_team_stats(home_team: str, away_team: str, bdl_api_key: str) -> dict:
    """
    Returns a unified stats dict for both teams in a matchup.

    {
        "home": { <stats for home team> },
        "away": { <stats for away team> }
    }

    Each team block:
    {
        "name":               "Boston Celtics",
        "season_win_pct":     0.71,
        "last_10_win_pct":    0.80,
        "home_win_pct":       0.78,   # only home games
        "away_win_pct":       0.61,   # only away games
        "avg_point_diff":     +8.3,   # positive = winning by X on average
        "avg_points_for":     118.4,
        "avg_points_against": 110.1,
        "recent_form":        [1,1,0,1,0],
        "rest_days":          2
    }
    """
    all_teams = _bdl_get_all_teams(bdl_api_key)

    home_bdl  = _find_team(all_teams, home_team)
    away_bdl  = _find_team(all_teams, away_team)

    home_games = _bdl_get_recent_games(home_bdl["id"], bdl_api_key)
    away_games = _bdl_get_recent_games(away_bdl["id"], bdl_api_key)

    return {
        "home": {"name": home_team, **_summarise_games(home_bdl["id"], home_games)},
        "away": {"name": away_team, **_summarise_games(away_bdl["id"], away_games)},
    }


# ── balldontlie helpers ───────────────────────────────────────────────────────
def _bdl_get_all_teams(api_key: str) -> list[dict]:
    """Fetches the full list of NBA teams."""
    resp = requests.get(
        f"{BALLDONTLIE_BASE}/teams",
        headers={"Authorization": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def _find_team(teams: list[dict], name: str) -> dict:
    """
    Finds a team by full name, city, nickname, or abbreviation.
    Falls back to fuzzy word matching. Raises ValueError if not found.
    """
    name_lower = name.lower()
    for t in teams:
        if name_lower in (
            t["full_name"].lower(),
            t["city"].lower(),
            t["name"].lower(),
            t["abbreviation"].lower(),
        ):
            return t
    # fuzzy fallback
    for t in teams:
        if any(word in t["full_name"].lower() for word in name_lower.split() if len(word) > 3):
            return t
    raise ValueError(f"Team not found in balldontlie: '{name}'")


def _bdl_get_recent_games(team_id: int, api_key: str) -> list[dict]:
    """
    Fetches all completed games for a team in the last LOOKBACK_DAYS days,
    sorted most-recent first.
    """
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=LOOKBACK_DAYS)

    resp = requests.get(
        f"{BALLDONTLIE_BASE}/games",
        headers={"Authorization": api_key},
        params={
            "team_ids[]": team_id,
            "start_date": start.isoformat(),
            "end_date":   today.isoformat(),
            "per_page":   100,
        },
        timeout=10,
    )
    resp.raise_for_status()

    games    = resp.json()["data"]
    finished = [g for g in games if g["status"] == "Final"]
    finished.sort(key=lambda g: g["date"], reverse=True)
    return finished


# ── Stats derivation ──────────────────────────────────────────────────────────
def _summarise_games(team_id: int, games: list[dict]) -> dict:
    """
    Derives all stats from raw game list. Splits home/away automatically.
    """
    if not games:
        return _empty_stats()

    all_results, home_results, away_results = [], [], []
    point_diffs, points_for, points_against = [], [], []

    for g in games:
        is_home    = g["home_team"]["id"] == team_id
        team_score = g["home_team_score"]    if is_home else g["visitor_team_score"]
        opp_score  = g["visitor_team_score"] if is_home else g["home_team_score"]

        won  = 1 if team_score > opp_score else 0
        diff = team_score - opp_score

        all_results.append(won)
        point_diffs.append(diff)
        points_for.append(team_score)
        points_against.append(opp_score)

        if is_home:
            home_results.append(won)
        else:
            away_results.append(won)

    last_date = datetime.fromisoformat(games[0]["date"].replace("Z", "+00:00")).date()
    rest_days = (datetime.now(timezone.utc).date() - last_date).days
    n         = len(all_results)

    return {
        "season_win_pct":     _pct(sum(all_results), n),
        "last_10_win_pct":    _pct(sum(all_results[:10]), min(n, 10)),
        "home_win_pct":       _pct(sum(home_results), len(home_results)),
        "away_win_pct":       _pct(sum(away_results), len(away_results)),
        "avg_point_diff":     round(sum(point_diffs) / n, 2),
        "avg_points_for":     round(sum(points_for) / n, 2),
        "avg_points_against": round(sum(points_against) / n, 2),
        "recent_form":        all_results[:5],
        "rest_days":          rest_days,
    }


def _pct(wins: int, total: int):
    return round(wins / total, 4) if total > 0 else None


def _empty_stats() -> dict:
    return {
        "season_win_pct":     None,
        "last_10_win_pct":    None,
        "home_win_pct":       None,
        "away_win_pct":       None,
        "avg_point_diff":     None,
        "avg_points_for":     None,
        "avg_points_against": None,
        "recent_form":        [],
        "rest_days":          None,
    }


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, json
    from dotenv import load_dotenv
    load_dotenv()

    bdl_key = os.getenv("BALLDONTLIE_API_KEY")
    if not bdl_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    home, away = "Boston Celtics", "Miami Heat"
    print(f"Fetching stats for: {away} @ {home}\n")

    stats = get_team_stats(home, away, bdl_key)
    print(json.dumps(stats, indent=2))