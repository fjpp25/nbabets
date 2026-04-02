"""
nba_stats.py
Fetches team performance data exclusively from balldontlie.io.

Derived stats (all computed from raw game results):
  H2H model features:
    season_win_pct, last_10_win_pct, home_win_pct, away_win_pct,
    avg_point_diff, avg_points_for, avg_points_against,
    recent_form, rest_days

  Totals model features (additional):
    avg_total_points      avg combined score per game
    last_10_avg_total     recent scoring pace
    avg_points_for_home   scoring at home specifically
    avg_points_for_away   scoring away specifically
    over_rate             how often games go over the midpoint total

  Spread model features (additional):
    avg_point_diff        already in H2H features
    home_avg_point_diff   point diff in home games only
    away_avg_point_diff   point diff in away games only
    last_10_point_diff    recent margin trend
"""

import requests
from datetime import datetime, timezone, timedelta


# ── Constants ─────────────────────────────────────────────────────────────────
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"
LOOKBACK_DAYS    = 90


# ── Public interface ──────────────────────────────────────────────────────────
def get_team_stats(home_team: str, away_team: str, bdl_api_key: str) -> dict:
    """
    Returns a unified stats dict for both teams in a matchup.
    Includes all features needed for H2H, totals, and spread models.

    {
        "home": { <full stats for home team> },
        "away": { <full stats for away team> }
    }
    """
    all_teams  = _bdl_get_all_teams(bdl_api_key)
    home_bdl   = _find_team(all_teams, home_team)
    away_bdl   = _find_team(all_teams, away_team)

    home_games = _bdl_get_recent_games(home_bdl["id"], bdl_api_key)
    away_games = _bdl_get_recent_games(away_bdl["id"], bdl_api_key)

    return {
        "home": {"name": home_team, **_summarise_games(home_bdl["id"], home_games)},
        "away": {"name": away_team, **_summarise_games(away_bdl["id"], away_games)},
    }


# ── balldontlie helpers ───────────────────────────────────────────────────────
_teams_cache: list[dict] = []

def _bdl_get_all_teams(api_key: str) -> list[dict]:
    global _teams_cache
    if _teams_cache:
        return _teams_cache
    for attempt in range(4):
        resp = requests.get(
            f"{BALLDONTLIE_BASE}/teams",
            headers={"Authorization": api_key},
            timeout=10,
        )
        if resp.status_code == 429:
            import time; time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        _teams_cache = resp.json()["data"]
        return _teams_cache
    raise RuntimeError("Could not fetch teams after retries")


def _find_team(teams: list[dict], name: str) -> dict:
    name_lower = name.lower()
    for t in teams:
        if name_lower in (
            t["full_name"].lower(),
            t["city"].lower(),
            t["name"].lower(),
            t["abbreviation"].lower(),
        ):
            return t
    for t in teams:
        if any(word in t["full_name"].lower() for word in name_lower.split() if len(word) > 3):
            return t
    raise ValueError(f"Team not found in balldontlie: '{name}'")


_games_cache: dict[int, list[dict]] = {}

def _bdl_get_recent_games(team_id: int, api_key: str) -> list[dict]:
    if team_id in _games_cache:
        return _games_cache[team_id]

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=LOOKBACK_DAYS)

    for attempt in range(4):
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
        if resp.status_code == 429:
            import time; time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        games    = resp.json()["data"]
        finished = [g for g in games if g["status"] == "Final"]
        finished.sort(key=lambda g: g["date"], reverse=True)
        _games_cache[team_id] = finished
        return finished

    raise RuntimeError(f"Could not fetch games for team {team_id} after retries")


# ── Stats derivation ──────────────────────────────────────────────────────────
def _summarise_games(team_id: int, games: list[dict]) -> dict:
    """
    Derives all stats from raw game list for all three models.
    """
    if not games:
        return _empty_stats()

    all_results,  home_results,  away_results  = [], [], []
    point_diffs,  home_diffs,    away_diffs    = [], [], []
    pts_for,      pts_against                  = [], []
    home_pts_for, away_pts_for                 = [], []
    total_points                               = []

    for g in games:
        is_home    = g["home_team"]["id"] == team_id
        team_score = g["home_team_score"]    if is_home else g["visitor_team_score"]
        opp_score  = g["visitor_team_score"] if is_home else g["home_team_score"]

        won  = 1 if team_score > opp_score else 0
        diff = team_score - opp_score
        tot  = team_score + opp_score

        all_results.append(won)
        point_diffs.append(diff)
        pts_for.append(team_score)
        pts_against.append(opp_score)
        total_points.append(tot)

        if is_home:
            home_results.append(won)
            home_diffs.append(diff)
            home_pts_for.append(team_score)
        else:
            away_results.append(won)
            away_diffs.append(diff)
            away_pts_for.append(team_score)

    last_date = datetime.fromisoformat(games[0]["date"].replace("Z", "+00:00")).date()
    rest_days = (datetime.now(timezone.utc).date() - last_date).days
    n         = len(all_results)

    # over_rate: how often this team's games exceed the season avg total
    # useful as a pace proxy — high over_rate = fast-paced team
    avg_total    = sum(total_points) / n
    median_total = sorted(total_points)[n // 2]
    over_rate    = sum(1 for t in total_points if t > median_total) / n

    is_b2b = (rest_days == 0)   # back-to-back: played yesterday

    return {
        # ── H2H features ──────────────────────────────────────────────────────
        "season_win_pct":       _pct(sum(all_results), n),
        "last_10_win_pct":      _pct(sum(all_results[:10]), min(n, 10)),
        "home_win_pct":         _pct(sum(home_results), len(home_results)),
        "away_win_pct":         _pct(sum(away_results), len(away_results)),
        "avg_point_diff":       _avg(point_diffs),
        "avg_points_for":       _avg(pts_for),
        "avg_points_against":   _avg(pts_against),
        "recent_form":          all_results[:5],
        "rest_days":            rest_days,
        "is_b2b":               is_b2b,

        # ── Spread features ───────────────────────────────────────────────────
        "home_avg_point_diff":  _avg(home_diffs),
        "away_avg_point_diff":  _avg(away_diffs),
        "last_10_point_diff":   _avg(point_diffs[:10]),

        # ── Totals features ───────────────────────────────────────────────────
        "avg_total_points":     round(avg_total, 2),
        "last_10_avg_total":    _avg([g["home_team_score"] + g["visitor_team_score"]
                                      for g in games[:10]]),
        "avg_points_for_home":  _avg(home_pts_for),
        "avg_points_for_away":  _avg(away_pts_for),
        "over_rate":            round(over_rate, 4),
    }


def _pct(wins: int, total: int):
    return round(wins / total, 4) if total > 0 else None


def _avg(values: list) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _empty_stats() -> dict:
    return {
        "season_win_pct":       None,
        "last_10_win_pct":      None,
        "home_win_pct":         None,
        "away_win_pct":         None,
        "avg_point_diff":       None,
        "avg_points_for":       None,
        "avg_points_against":   None,
        "recent_form":          [],
        "rest_days":            None,
        "home_avg_point_diff":  None,
        "away_avg_point_diff":  None,
        "last_10_point_diff":   None,
        "avg_total_points":     None,
        "last_10_avg_total":    None,
        "avg_points_for_home":  None,
        "avg_points_for_away":  None,
        "over_rate":            None,
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
