"""
player_props.py
Fetches player prop odds from The Odds API and compares them against
recent player performance from balldontlie to find value bets.

Markets covered:
  - player_points    (points scored)
  - player_rebounds  (total rebounds)
  - player_assists   (assists)

Approach:
  For each prop line, we compute the player's rolling average and
  standard deviation over their last N games. We flag a bet when:
    1. The rolling average meaningfully disagrees with the book line
    2. The player has enough recent games to be reliable (MIN_GAMES)
    3. The best available odds meet our minimum threshold
"""

import time
import requests
from value_detector import compute_risk_score
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv


# ── Constants ─────────────────────────────────────────────────────────────────
BALLDONTLIE_BASE  = "https://api.balldontlie.io/v1"
ODDS_BASE         = "https://api.the-odds-api.com/v4"
SPORT_KEY         = "basketball_nba"
REGIONS           = "eu"
ODDS_FORMAT       = "decimal"

PROP_MARKETS      = [
    "player_points",
    "player_rebounds",
    "player_assists",
]

LOOKBACK_GAMES    = 10     # games to compute rolling average from
MIN_GAMES         = 6      # minimum games needed to consider a prop
MIN_EDGE_PTS      = 2.0    # rolling avg must differ from line by this many units
MIN_ODDS          = 1.75   # minimum odds to flag a prop bet
SIMULATED_STAKE   = 10.0

ET_OFFSET = timedelta(hours=-5)


# ── Public interface ──────────────────────────────────────────────────────────
def fetch_player_props(odds_api_key: str, games: list[dict]) -> list[dict]:
    """
    Fetches player prop odds for all of today's games.
    Returns a list of prop dicts, one per player per market per game.

    Each prop:
    {
        "game":        "Miami Heat @ Boston Celtics",
        "game_id":     "abc123",   ← Odds API game id
        "player":      "Jayson Tatum",
        "market":      "player_points",
        "line":        27.5,
        "over_odds":   1.91,
        "under_odds":  1.91,
        "bookmaker":   "Pinnacle",
    }
    """
    props = []
    for game in games:
        game_props = _fetch_game_props(odds_api_key, game)
        props.extend(game_props)
        time.sleep(0.5)   # be polite to the API
    return props


def find_prop_value_bets(
    props:         list[dict],
    bdl_api_key:   str,
    injury_report: list[dict] = None,
) -> list[dict]:
    """
    For each prop, fetches the player's recent stats from balldontlie
    and checks if the rolling average beats the MIN_EDGE_PTS threshold.

    Skips props for players listed as Out or Doubtful in the injury report.
    Returns value bets sorted by edge (largest first).
    """
    # build set of injured player names to skip
    injured_out = set()
    if injury_report:
        for inj in injury_report:
            if inj.get("status") in {"Out", "Doubtful"}:
                injured_out.add(inj["player_name"].lower())

    # cache player stats to avoid redundant API calls
    player_cache: dict[str, dict] = {}
    value_bets = []

    print(f"\n  Analysing {len(props)} prop lines...")
    seen_players = set()

    for prop in props:
        player_name = prop["player"]
        market      = prop["market"]
        stat_key    = _market_to_stat(market)

        # skip props for players who are out or doubtful
        if player_name.lower() in injured_out:
            continue

        # fetch stats once per player
        cache_key = f"{player_name}_{market}"
        if cache_key not in player_cache:
            if player_name not in seen_players:
                seen_players.add(player_name)
            stats = _get_player_recent_stats(player_name, stat_key, bdl_api_key)
            player_cache[cache_key] = stats

        stats = player_cache[cache_key]
        if not stats or stats["games"] < MIN_GAMES:
            continue

        # validate player's team is actually in this game
        player_team = stats.get("team", "")
        if player_team and not _team_in_game(player_team, prop["game"]):
            continue   # wrong player matched — skip

        rolling_avg = stats["rolling_avg"]
        line        = prop["line"]
        edge        = rolling_avg - line   # positive = lean Over, negative = lean Under

        if abs(edge) < MIN_EDGE_PTS:
            continue

        # pick the side with the best odds
        if edge > 0:
            side      = "Over"
            best_odds = prop["over_odds"]
        else:
            side      = "Under"
            best_odds = prop["under_odds"]

        if not best_odds or best_odds < MIN_ODDS:
            continue

        _risk = compute_risk_score(
            model_prob=0.5 + abs(edge) / (2 * max(abs(edge), 1)),
            odds=best_odds,
            rest_days=2,
            volatility=stats["rolling_std"],
            sample_size=stats["games"],
        )
        value_bets.append({
            "market":           "props",
            "prop_market":      market,
            "game":             prop["game"],
            "commence_time":    prop["commence_time"],
            "player":           player_name,
            "stat":             stat_key,
            "bet_label":        f"{player_name} {side} {line} {_market_label(market)}",
            "side":             side,
            "line":             line,
            "rolling_avg":      round(rolling_avg, 1),
            "rolling_std":      round(stats["rolling_std"], 1),
            "games_sampled":    stats["games"],
            "team":             stats.get("team", ""),
            "edge":             round(abs(edge), 1),
            "best_odds":        best_odds,
            "bookmaker":        prop["bookmaker"],
            "risk_score":       _risk["score"],
            "risk_label":       _risk["label"],
            "risk_components":  _risk["components"],
            "simulated_stake":  SIMULATED_STAKE,
            "simulated_return": round(SIMULATED_STAKE * best_odds, 2),
            "simulated_profit": round(SIMULATED_STAKE * best_odds - SIMULATED_STAKE, 2),
            "outcome":          None,
            "actual_value":     None,
            "actual_pnl":       None,
        })

    value_bets.sort(key=lambda b: -b["edge"])
    return value_bets


def summarise_prop_bets(prop_bets: list[dict]) -> None:
    """Prints a formatted summary of prop value bets."""
    if not prop_bets:
        print("  No prop value bets found today.")
        return

    # group by prop type
    for market in PROP_MARKETS:
        bets = [b for b in prop_bets if b["prop_market"] == market]
        if not bets:
            continue

        label = _market_label(market).upper()
        print(f"\n  ── {label} PROPS ({len(bets)} bet(s)) {'─'*35}")
        for i, bet in enumerate(bets, 1):
            print(f"\n  #{i}  {bet['game']}")
            team_str = f" ({bet['team']})" if bet.get('team') else ""
            print(f"       Player:   {bet['player']}{team_str}")
            print(f"       Bet:      {bet['side']} {bet['line']} {_market_label(bet['prop_market'])}")
            print(f"       Odds:     {bet['best_odds']} ({bet['bookmaker']})")
            print(f"       Avg:      {bet['rolling_avg']} over last "
                  f"{bet['games_sampled']} games  "
                  f"(σ={bet['rolling_std']})  |  Edge: {bet['edge']:+.1f}")
            print(f"       Simulated: €{bet['simulated_stake']:.2f} stake → "
                  f"€{bet['simulated_return']:.2f} return "
                  f"(€{bet['simulated_profit']:+.2f} if correct)")
            risk_score = bet.get("risk_score")
            risk_label = bet.get("risk_label", "—")
            if risk_score is not None:
                print(f"       Risk:      {risk_score}/100 ({risk_label})")

    total_staked = sum(b["simulated_stake"]  for b in prop_bets)
    total_return = sum(b["simulated_return"] for b in prop_bets)
    total_profit = sum(b["simulated_profit"] for b in prop_bets)
    print(f"\n  {'─'*60}")
    print(f"  Props total simulated stake:  €{total_staked:.2f}")
    print(f"  Props total potential return: €{total_return:.2f}")
    print(f"  Props total potential profit: €{total_profit:+.2f}")
    print(f"  {'='*60}\n")


# ── Odds API fetchers ─────────────────────────────────────────────────────────
def _fetch_game_props(api_key: str, game: dict) -> list[dict]:
    """Fetches all prop markets for a single game."""
    url    = f"{ODDS_BASE}/sports/{SPORT_KEY}/events/{game['id']}/odds"
    params = {
        "apiKey":     api_key,
        "regions":    REGIONS,
        "markets":    ",".join(PROP_MARKETS),
        "oddsFormat": ODDS_FORMAT,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 404:
            return []   # no props available for this game
        resp.raise_for_status()
    except Exception as e:
        print(f"    [props] Could not fetch props for {game['home_team']}: {e}")
        return []

    remaining = resp.headers.get("x-requests-remaining", "?")
    used      = resp.headers.get("x-requests-used", "?")
    print(f"    [Odds API props] {game['away_team']} @ {game['home_team']} "
          f"— used: {used} | remaining: {remaining}")

    props = []
    data  = resp.json()
    for bm in data.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market["key"] not in PROP_MARKETS:
                continue
            # group outcomes by player (each player has Over + Under)
            players: dict[str, dict] = {}
            for outcome in market["outcomes"]:
                pname = outcome["description"]   # player name
                side  = outcome["name"]          # "Over" or "Under"
                if pname not in players:
                    players[pname] = {"over": None, "under": None, "line": outcome["point"]}
                if side == "Over":
                    players[pname]["over"]  = outcome["price"]
                else:
                    players[pname]["under"] = outcome["price"]
                players[pname]["line"] = outcome["point"]

            for pname, pdata in players.items():
                if pdata["over"] and pdata["under"]:
                    props.append({
                        "game":          f"{game['away_team']} @ {game['home_team']}",
                        "game_id":       game["id"],
                        "commence_time": game["commence_time"],
                        "player":        pname,
                        "market":        market["key"],
                        "line":          pdata["line"],
                        "over_odds":     pdata["over"],
                        "under_odds":    pdata["under"],
                        "bookmaker":     bm["title"],
                    })

    # deduplicate: keep best over/under odds per player per market
    return _best_props(props)


def _best_props(props: list[dict]) -> list[dict]:
    """Keeps the best over and under odds per player+market combination."""
    best: dict[str, dict] = {}
    for p in props:
        key = f"{p['player']}_{p['market']}_{p['game_id']}"
        if key not in best:
            best[key] = p.copy()
        else:
            if p["over_odds"]  > best[key]["over_odds"]:
                best[key]["over_odds"]  = p["over_odds"]
                best[key]["bookmaker"]  = p["bookmaker"]
            if p["under_odds"] > best[key]["under_odds"]:
                best[key]["under_odds"] = p["under_odds"]
    return list(best.values())


# ── balldontlie stat fetcher ──────────────────────────────────────────────────
def _get_player_recent_stats(
    player_name: str,
    stat_key:    str,
    api_key:     str,
) -> dict | None:
    """
    Fetches recent game stats for a player and computes rolling average + std.
    Returns None if player not found or insufficient data.
    """
    # Step 1: find player id
    player = _search_player(player_name, api_key)
    if not player:
        return None

    # Step 2: fetch recent game stats
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=60)

    try:
        resp = requests.get(
            f"{BALLDONTLIE_BASE}/stats",
            headers={"Authorization": api_key},
            params={
                "player_ids[]": player["id"],
                "start_date":   start.isoformat(),
                "end_date":     today.isoformat(),
                "per_page":     LOOKBACK_GAMES,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        return None

    game_stats = resp.json()["data"]
    # filter out games where player didn't play (min 10 minutes)
    played = [g for g in game_stats if (g.get("min") or "0") not in ("", "0", "00", None)
              and g.get(stat_key) is not None]

    if len(played) < MIN_GAMES:
        return None

    values    = [float(g[stat_key]) for g in played[:LOOKBACK_GAMES]]
    avg       = sum(values) / len(values)
    std       = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
    team_name = played[0].get("team", {}).get("full_name", "") if played else ""

    return {
        "rolling_avg": avg,
        "rolling_std": std,
        "games":       len(values),
        "values":      values,
        "team":        team_name,
    }


def _search_player(name: str, api_key: str) -> dict | None:
    """Searches balldontlie for a player by name."""
    # try last name first for efficiency
    last_name = name.split()[-1]
    try:
        resp = requests.get(
            f"{BALLDONTLIE_BASE}/players",
            headers={"Authorization": api_key},
            params={"search": last_name, "per_page": 10},
            timeout=10,
        )
        resp.raise_for_status()
        players = resp.json()["data"]
    except Exception:
        return None

    # find best name match
    name_lower = name.lower()
    for p in players:
        full = f"{p['first_name']} {p['last_name']}".lower()
        if full == name_lower:
            return p
    # fuzzy: check if all name parts match
    name_parts = set(name_lower.split())
    for p in players:
        full_parts = {p["first_name"].lower(), p["last_name"].lower()}
        if name_parts & full_parts == name_parts:
            return p
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _team_in_game(team_name: str, game_str: str) -> bool:
    """
    Checks whether a team name appears in a game string like
    'Miami Heat @ Boston Celtics'. Uses fuzzy word matching.
    """
    team_words = {w.lower() for w in team_name.split() if len(w) > 3}
    game_words = {w.lower() for w in game_str.split() if len(w) > 3}
    return bool(team_words & game_words)


def _market_to_stat(market: str) -> str:
    return {
        "player_points":   "pts",
        "player_rebounds": "reb",
        "player_assists":  "ast",
    }.get(market, "pts")


def _market_label(market: str) -> str:
    return {
        "player_points":   "Points",
        "player_rebounds": "Rebounds",
        "player_assists":  "Assists",
    }.get(market, market)


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from odds_fetcher import fetch_todays_games
    load_dotenv()

    odds_key = os.getenv("ODDS_API_KEY")
    bdl_key  = os.getenv("BALLDONTLIE_API_KEY")
    if not odds_key or not bdl_key:
        raise ValueError("Missing API keys in .env")

    print("Fetching today's games...")
    games = fetch_todays_games(odds_key)
    print(f"  {len(games)} games found\n")

    print("Fetching player props...")
    props = fetch_player_props(odds_key, games)
    print(f"  {len(props)} prop lines found\n")

    print("Finding value bets...")
    bets = find_prop_value_bets(props, bdl_key)
    print(f"  {len(bets)} prop value bets found")
    summarise_prop_bets(bets)
