"""
injuries.py
Fetches the current NBA injury report from balldontlie.io and computes
an injury-adjusted win probability modifier for each team in a matchup.

How it works:
  1. Fetch all active injuries from /nba/v1/player_injuries
  2. For each player on an injured team, fetch their season scoring average
     to classify them as Star / Key / Role player
  3. Apply a win probability adjustment based on status and player tier
  4. Flag games as "injury-affected" when significant players are out

Injury impact table:
  Status      | Star (-25 ppg+) | Key (12-25 ppg) | Role (<12 ppg)
  ------------|-----------------|-----------------|---------------
  Out         |     -10%        |      -5%        |     -1%
  Doubtful    |      -6%        |      -3%        |     -0.5%
  Questionable|      -3%        |      -1.5%      |     -0.5%
  Probable    |      -1%        |      -0.5%      |      0%
"""

import time
import json as _json
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta


# ── Constants ─────────────────────────────────────────────────────────────────
BALLDONTLIE_BASE = "https://api.balldontlie.io"
CURRENT_SEASON   = 2025   # balldontlie uses start year

# Points per game thresholds for player tier classification
STAR_PPG_THRESHOLD = 20.0
KEY_PPG_THRESHOLD  = 12.0
MIN_PPG_THRESHOLD  =  5.0   # ignore players below this — bench/garbage time

# Win probability adjustments per status per tier (as decimals)
INJURY_IMPACT = {
    "Out":         {"star": -0.10, "key": -0.05, "role": -0.01},
    "Doubtful":    {"star": -0.06, "key": -0.03, "role": -0.005},
    "Questionable":{"star": -0.03, "key": -0.015,"role": -0.005},
    "Probable":    {"star": -0.01, "key": -0.005,"role":  0.0},
}

# Total points adjustments per status per tier (points per game impact)
# Missing a star scorer reduces combined scoring; missing a role player barely matters
INJURY_TOTAL_IMPACT = {
    "Out":         {"star": -4.0, "key": -2.0, "role": -0.5},
    "Doubtful":    {"star": -2.5, "key": -1.2, "role": -0.3},
    "Questionable":{"star": -1.0, "key": -0.5, "role":  0.0},
    "Probable":    {"star":  0.0, "key":  0.0, "role":  0.0},
}

# Statuses that meaningfully affect game outcome
SIGNIFICANT_STATUSES = {"Out", "Doubtful", "Questionable"}


# ── Public interface ──────────────────────────────────────────────────────────
def get_injury_report(api_key: str) -> list[dict]:
    _load_ppg_cache()  # warm up cache from disk before any lookups
    """
    Fetches all current NBA injuries from balldontlie.

    Returns a list of injury dicts:
    {
        "player_id":   12345,
        "player_name": "Jayson Tatum",
        "team_id":     2,
        "status":      "Out",
        "description": "Right Achilles - Management",
        "return_date": "TBD",
    }
    """
    injuries = []
    cursor   = None

    while True:
        params = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            f"{BALLDONTLIE_BASE}/nba/v1/player_injuries",
            headers={"Authorization": api_key},
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        for item in data["data"]:
            player = item.get("player", {})
            injuries.append({
                "player_id":   player.get("id"),
                "player_name": f"{player.get('first_name','')} {player.get('last_name','')}".strip(),
                "team_id":     player.get("team_id"),
                "status":      item.get("status", ""),
                "description": item.get("description", ""),
                "return_date": item.get("return_date", ""),
            })

        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor
        time.sleep(0.5)

    return injuries


def get_team_injury_impact(
    home_team_id: int,
    away_team_id: int,
    injuries:     list[dict],
    api_key:      str,
) -> dict:
    """
    Computes injury-adjusted win probability modifiers for both teams.

    Returns:
    {
        "home": {
            "adjustment":   -0.08,      # subtract from home win prob
            "injuries":     [...],      # list of significant injuries
            "affected":     True,       # True if any Out/Doubtful player
        },
        "away": { ... }
    }
    """
    home_injuries = [i for i in injuries if i["team_id"] == home_team_id
                     and i["status"] in SIGNIFICANT_STATUSES]
    away_injuries = [i for i in injuries if i["team_id"] == away_team_id
                     and i["status"] in SIGNIFICANT_STATUSES]

    home_result = _compute_team_impact(home_injuries, api_key)
    away_result = _compute_team_impact(away_injuries, api_key)

    return {"home": home_result, "away": away_result}


def apply_injury_adjustments(
    home_win_prob: float,
    away_win_prob: float,
    injury_impact: dict,
) -> tuple[float, float]:
    """
    Applies injury adjustments to raw model probabilities and
    renormalises so they sum to 1.0.

    Example:
        Home model prob: 0.65, home adjustment: -0.08 (star out)
        Away model prob: 0.35, away adjustment: -0.03 (key player doubtful)
        Raw adjusted:    home 0.57, away 0.32
        Renormalised:    home 0.64, away 0.36
    """
    home_adj = home_win_prob + injury_impact["home"]["adjustment"]
    away_adj = away_win_prob + injury_impact["away"]["adjustment"]

    # clamp to [0.05, 0.95] before normalising
    home_adj = max(0.05, min(0.95, home_adj))
    away_adj = max(0.05, min(0.95, away_adj))

    # renormalise
    total    = home_adj + away_adj
    return round(home_adj / total, 4), round(away_adj / total, 4)


def format_injury_summary(team_injuries: dict, team_name: str) -> str:
    """Returns a one-line summary of significant injuries for display."""
    if not team_injuries["injuries"]:
        return f"  {team_name}: No significant injuries"

    lines = [f"  {team_name} (adj {team_injuries['adjustment']:+.1%}):"]
    for inj in team_injuries["injuries"]:
        tier   = inj["tier"].upper()
        status = inj["status"]
        name   = inj["player_name"]
        ppg    = inj["ppg"]
        lines.append(f"    [{tier}] {name} ({ppg:.1f} ppg) — {status}")
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _compute_team_impact(team_injuries: list[dict], api_key: str) -> dict:
    """
    Computes total adjustment and tier info for a team's injuries.
    Only fetches PPG for players on this specific team — not all injured players.
    """
    if not team_injuries:
        return {"adjustment": 0.0, "injuries": [], "affected": False}

    total_adjustment = 0.0
    enriched         = []

    for inj in team_injuries:
        ppg  = _get_player_ppg(inj["player_id"], api_key, inj.get("player_name", ""))

        # skip players with minimal impact — bench/garbage time players
        # Note: 0.0 ppg also catches lookup failures, so we keep those
        # only if status is Out/Doubtful for a named player (may be misclassified)
        if ppg > 0.0 and ppg < MIN_PPG_THRESHOLD:
            continue

        tier = _classify_player(ppg)

        # if ppg lookup failed (0.0) and status is only Questionable, skip
        if ppg == 0.0 and inj["status"] == "Questionable":
            continue

        impact_table = INJURY_IMPACT.get(inj["status"], {})
        adjustment   = impact_table.get(tier, 0.0)
        total_adjustment += adjustment

        enriched.append({
            **inj,
            "ppg":        ppg,
            "tier":       tier,
            "adjustment": adjustment,
        })

    # sort by impact magnitude (most impactful first)
    enriched.sort(key=lambda x: abs(x["adjustment"]), reverse=True)

    total_pts_adjustment = sum(
        INJURY_TOTAL_IMPACT.get(i["status"], {}).get(i["tier"], 0.0)
        for i in enriched
    )

    return {
        "adjustment":      round(total_adjustment, 4),
        "pts_adjustment":  round(total_pts_adjustment, 2),
        "injuries":        enriched,
        "affected":        any(i["status"] in {"Out", "Doubtful"} for i in enriched),
    }


# PPG cache — persisted to disk so players are only looked up once per day
_ppg_cache: dict[int, float] = {}


def _load_ppg_cache() -> None:
    """Loads the PPG cache from disk if it exists and is from today."""
    global _ppg_cache
    try:
        path = Path("data/ppg_cache.json")
        if path.exists():
            with open(path) as f:
                data = _json.load(f)
            # only use cache if it was saved today
            saved_date = data.get("_date", "")
            today = datetime.now(timezone.utc).date().isoformat()
            if saved_date == today:
                _ppg_cache = {int(k): v for k, v in data.items() if k != "_date"}
    except Exception:
        pass


def _save_ppg_cache() -> None:
    """Saves the PPG cache to disk."""
    try:
        path = Path("data/ppg_cache.json")
        path.parent.mkdir(exist_ok=True)
        today = datetime.now(timezone.utc).date().isoformat()
        with open(path, "w") as f:
            _json.dump({**{str(k): v for k, v in _ppg_cache.items()}, "_date": today}, f)
    except Exception:
        pass


# Minimum games played to trust a season average
# prevents partial-season/injury-riddled averages from misclassifying stars
MIN_GAMES_FOR_AVG = 10


def _get_player_ppg(player_id: int, api_key: str, player_name: str = "") -> float:
    """
    Fetches a player's season average points per game.

    Uses strict full-name matching and a minimum games filter to prevent
    misclassification of stars who missed time (e.g. Curry on a partial season).
    Falls back across seasons until a reliable average is found.
    """
    if not player_id and not player_name:
        return 0.0

    if player_id in _ppg_cache:
        return _ppg_cache[player_id]

    # Strategy 1: search by full name for canonical player ID
    if player_name:
        try:
            time.sleep(1.1)
            last_name = player_name.split()[-1]
            resp = requests.get(
                f"{BALLDONTLIE_BASE}/nba/v1/players",
                headers={"Authorization": api_key},
                params={"search": last_name, "per_page": 10},
                timeout=10,
            )
            resp.raise_for_status()
            players = resp.json().get("data", [])

            # strict full name match first, then partial
            name_lower = player_name.lower()
            canonical_id = None
            for p in players:
                full = f"{p['first_name']} {p['last_name']}".lower()
                if full == name_lower:          # exact match
                    canonical_id = p["id"]
                    break
            if not canonical_id:
                for p in players:
                    full = f"{p['first_name']} {p['last_name']}".lower()
                    parts = name_lower.split()
                    if len(parts) >= 2 and parts[0] in full and parts[-1] in full:
                        canonical_id = p["id"]
                        break

            if canonical_id:
                # try current and last 2 seasons, take best with MIN_GAMES
                best_ppg = 0.0
                for season in [CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                    time.sleep(1.1)
                    r = requests.get(
                        f"{BALLDONTLIE_BASE}/nba/v1/season_averages",
                        headers={"Authorization": api_key},
                        params={"season": season, "player_id": canonical_id},
                        timeout=10,
                    )
                    r.raise_for_status()
                    data = r.json().get("data", [])
                    if data:
                        games_played = int(data[0].get("games_played", 0) or 0)
                        ppg          = float(data[0].get("pts", 0.0) or 0.0)
                        # accept this season if enough games played
                        if games_played >= MIN_GAMES_FOR_AVG and ppg > 0:
                            best_ppg = ppg
                            break
                        # if not enough games, keep as candidate but try next season
                        elif ppg > best_ppg:
                            best_ppg = ppg

                if best_ppg > 0:
                    _ppg_cache[player_id] = best_ppg
                    _save_ppg_cache()
                    return best_ppg
        except Exception:
            pass

    # Strategy 2: direct ID lookup (fallback)
    if player_id:
        try:
            best_ppg = 0.0
            for season in [CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2]:
                time.sleep(1.1)
                r = requests.get(
                    f"{BALLDONTLIE_BASE}/nba/v1/season_averages",
                    headers={"Authorization": api_key},
                    params={"season": season, "player_id": player_id},
                    timeout=10,
                )
                r.raise_for_status()
                data = r.json().get("data", [])
                if data:
                    games_played = int(data[0].get("games_played", 0) or 0)
                    ppg          = float(data[0].get("pts", 0.0) or 0.0)
                    if games_played >= MIN_GAMES_FOR_AVG and ppg > 0:
                        best_ppg = ppg
                        break
                    elif ppg > best_ppg:
                        best_ppg = ppg
            if best_ppg > 0:
                _ppg_cache[player_id] = best_ppg
                _save_ppg_cache()
                return best_ppg
        except Exception:
            pass

    _ppg_cache[player_id] = 0.0
    return 0.0


def _classify_player(ppg: float) -> str:
    """Classifies a player as star / key / role based on scoring average."""
    if ppg >= STAR_PPG_THRESHOLD:
        return "star"
    elif ppg >= KEY_PPG_THRESHOLD:
        return "key"
    else:
        return "role"


# ── Team ID lookup ────────────────────────────────────────────────────────────
# Cache teams list to avoid repeated API calls
_teams_cache: list[dict] = []


def get_team_ids(home_team: str, away_team: str, api_key: str) -> tuple[int, int]:
    """
    Looks up balldontlie team IDs for both teams.
    Caches the teams list so it is only fetched once per session.
    Returns (home_id, away_id), or (None, None) on failure.
    """
    global _teams_cache
    try:
        if not _teams_cache:
            for attempt in range(3):
                resp = requests.get(
                    f"{BALLDONTLIE_BASE}/nba/v1/teams",
                    headers={"Authorization": api_key},
                    timeout=10,
                )
                if resp.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                _teams_cache = resp.json()["data"]
                break

        home_id = _find_team_id(_teams_cache, home_team)
        away_id = _find_team_id(_teams_cache, away_team)
        return home_id, away_id
    except Exception:
        return None, None


def _find_team_id(teams: list[dict], name: str) -> int | None:
    name_lower = name.lower()
    for t in teams:
        if name_lower in (
            t["full_name"].lower(),
            t["city"].lower(),
            t["name"].lower(),
            t["abbreviation"].lower(),
        ):
            return t["id"]
    for t in teams:
        if any(w in t["full_name"].lower() for w in name_lower.split() if len(w) > 3):
            return t["id"]
    return None


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, json
    from dotenv import load_dotenv
    load_dotenv()

    bdl_key = os.getenv("BALLDONTLIE_API_KEY")
    if not bdl_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    print("Fetching injury report...")
    injuries = get_injury_report(bdl_key)
    print(f"  {len(injuries)} players currently listed\n")

    # Show significant injuries
    significant = [i for i in injuries if i["status"] in SIGNIFICANT_STATUSES]
    print(f"  Significant ({len(significant)} players):")
    for inj in significant[:15]:
        print(f"    [{inj['status']:12s}] {inj['player_name']}")

    # Test impact on a matchup
    print("\nTesting impact: Boston Celtics @ Miami Heat")
    home_id, away_id = get_team_ids("Boston Celtics", "Miami Heat", bdl_key)
    print(f"  Team IDs — Celtics: {home_id}, Heat: {away_id}")

    if home_id and away_id:
        impact = get_team_injury_impact(home_id, away_id, injuries, bdl_key)
        print(f"\n{format_injury_summary(impact['home'], 'Boston Celtics')}")
        print(f"{format_injury_summary(impact['away'], 'Miami Heat')}")

        # Apply to a hypothetical prediction
        home_prob, away_prob = apply_injury_adjustments(0.65, 0.35, impact)
        print(f"\n  Raw model:     Celtics {0.65:.1%}  |  Heat {0.35:.1%}")
        print(f"  Injury-adj:    Celtics {home_prob:.1%}  |  Heat {away_prob:.1%}")
