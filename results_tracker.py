"""
results_tracker.py
Fetches last night's NBA results from balldontlie and updates the
picks JSON file with actual outcomes across all three markets:
  - H2H      (who won)
  - Spread   (did the predicted team cover)
  - Totals   (did the game go over or under)

Run each morning after games have finished.

Usage:
    python results_tracker.py              # updates yesterday's picks
    python results_tracker.py 2026-03-30   # updates a specific date
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv


DATA_DIR         = Path("data")
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"
ET_OFFSET        = timedelta(hours=-5)


# ── Main ──────────────────────────────────────────────────────────────────────
def run(date_str: str = None):
    load_dotenv()
    bdl_key = os.getenv("BALLDONTLIE_API_KEY")
    if not bdl_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    if not date_str:
        date_str = (datetime.now(timezone.utc) + ET_OFFSET - timedelta(days=1)).date().isoformat()

    picks_path = DATA_DIR / f"picks_{date_str}.json"
    if not picks_path.exists():
        print(f"No picks file found for {date_str} ({picks_path})")
        return

    print(f"\nUpdating results for {date_str}...")

    with open(picks_path) as f:
        picks = json.load(f)

    results = _fetch_results(date_str, bdl_key)
    if not results:
        print("  No completed games found yet — try again later.")
        return

    print(f"  {len(results)} completed game(s) found.\n")

    # ── Settle predictions ────────────────────────────────────────────────────
    h2h_correct = 0
    spread_correct = 0
    total_correct = 0
    settled = 0

    for pred in picks["predictions"]:
        result = _find_result(pred["home_team"], pred["away_team"], results)
        if not result:
            continue

        settled += 1
        actual_home_score = result["home_team_score"]
        actual_away_score = result["visitor_team_score"]
        actual_margin     = actual_home_score - actual_away_score
        actual_total      = actual_home_score + actual_away_score
        actual_winner     = (result["home_team"]["full_name"]
                             if actual_margin > 0
                             else result["visitor_team"]["full_name"])

        # H2H
        pred["h2h_actual_winner"] = actual_winner
        pred["h2h_correct"]       = (actual_winner == pred["h2h_predicted_winner"])
        if pred["h2h_correct"]:
            h2h_correct += 1

        # Spread — did the home team cover?
        # home covers if actual_margin > -book_line
        # e.g. home -5.5: covers if wins by 6+
        # e.g. home +5.5: covers if loses by 5 or less (or wins)
        pred["spread_actual_margin"] = actual_margin
        if pred["spread_book_line"] is not None:
            home_covered = actual_margin > (-pred["spread_book_line"])
            model_said_home_covers = pred["spread_covers"]
            pred["spread_covered"] = home_covered
            pred["spread_correct"] = (home_covered == model_said_home_covers)
            if pred["spread_correct"]:
                spread_correct += 1

        # Totals
        pred["total_actual"] = actual_total
        if pred["total_book_line"] is not None:
            went_over = actual_total > pred["total_book_line"]
            pred["total_went_over"] = went_over
            pred["total_correct"]   = (
                (went_over and pred["total_prediction"] == "Over") or
                (not went_over and pred["total_prediction"] == "Under")
            )
            if pred["total_correct"]:
                total_correct += 1

    # ── Settle value bets ─────────────────────────────────────────────────────
    total_staked = 0.0
    total_pnl    = 0.0

    for vb in picks["value_bets"]:
        parts     = vb["game"].split(" @ ")
        away_team = parts[0].strip()
        home_team = parts[1].strip()
        result    = _find_result(home_team, away_team, results)
        if not result:
            continue

        actual_home  = result["home_team_score"]
        actual_away  = result["visitor_team_score"]
        actual_margin = actual_home - actual_away
        actual_total  = actual_home + actual_away

        won = False
        if vb["market"] == "h2h":
            actual_winner = (result["home_team"]["full_name"]
                             if actual_margin > 0
                             else result["visitor_team"]["full_name"])
            won = _names_match(actual_winner, vb["bet_team"])

        elif vb["market"] == "spread":
            # check if bet team covered
            book_line = vb["book_line"]   # from home team's perspective
            if _names_match(vb["bet_team"], home_team):
                won = actual_margin > (-book_line)
            else:
                won = actual_margin < (-book_line)

        elif vb["market"] == "totals":
            if vb["bet_side"] == "Over":
                won = actual_total > vb["book_line"]
            else:
                won = actual_total < vb["book_line"]

        vb["outcome"]    = "won" if won else "lost"
        vb["actual_pnl"] = round(vb["simulated_profit"] if won
                                 else -vb["simulated_stake"], 2)
        total_staked    += vb["simulated_stake"]
        total_pnl       += vb["actual_pnl"]

    # ── Settle prop bets ─────────────────────────────────────────────────────────
    for pb in picks.get("prop_bets", []):
        if pb.get("actual_value") is not None:
            continue   # already settled
        parts     = pb["game"].split(" @ ")
        away_team = parts[0].strip()
        home_team = parts[1].strip()
        result    = _find_result(home_team, away_team, results)
        if not result:
            continue

        # fetch actual player stat from balldontlie
        actual = _fetch_player_stat(
            pb["player"], pb["stat"], result["id"], bdl_key
        )
        if actual is None:
            continue

        pb["actual_value"] = actual
        won = (actual > pb["line"] if pb["side"] == "Over"
               else actual < pb["line"])
        pb["outcome"]    = "won" if won else "lost"
        pb["actual_pnl"] = round(
            pb["simulated_profit"] if won else -pb["simulated_stake"], 2
        )
        total_staked += pb["simulated_stake"]
        total_pnl    += pb["actual_pnl"]

    # ── Settle contrarian picks ───────────────────────────────────────────────
    for cp in picks.get("contrarian_picks", []):
        parts     = cp["game"].split(" @ ")
        away_team = parts[0].strip()
        home_team = parts[1].strip()
        result    = _find_result(home_team, away_team, results)
        if not result:
            continue
        actual_winner = (result["home_team"]["full_name"]
                         if result["home_team_score"] > result["visitor_team_score"]
                         else result["visitor_team"]["full_name"])
        cp["correct"] = _names_match(actual_winner, cp["model_pick"])

    # ── Save summary ──────────────────────────────────────────────────────────
    picks["results_summary"] = {
        "settled":              settled,
        "h2h_correct":          h2h_correct,
        "h2h_accuracy":         round(h2h_correct / settled, 4) if settled else None,
        "spread_correct":       spread_correct,
        "spread_accuracy":      round(spread_correct / settled, 4) if settled else None,
        "total_correct":        total_correct,
        "total_accuracy":       round(total_correct / settled, 4) if settled else None,
        "value_bets_total":     len(picks["value_bets"]),
        "value_bets_won":       sum(1 for vb in picks["value_bets"]
                                    if vb.get("outcome") == "won"),
        "total_staked":         round(total_staked, 2),
        "total_pnl":            round(total_pnl, 2),
        "updated_at":           datetime.now(timezone.utc).isoformat(),
    }

    with open(picks_path, "w") as f:
        json.dump(picks, f, indent=2)

    _print_summary(picks, date_str)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fetch_results(date_str: str, api_key: str) -> list[dict]:
    """Fetches all Final games for a given ET date from balldontlie."""
    # balldontlie uses UTC dates — fetch both the ET date and the next UTC day
    # to catch late games that tip off after midnight UTC
    et_date  = datetime.strptime(date_str, "%Y-%m-%d").date()
    utc_next = (et_date + timedelta(days=1)).isoformat()

    all_games = []
    for fetch_date in [date_str, utc_next]:
        resp = requests.get(
            f"{BALLDONTLIE_BASE}/games",
            headers={"Authorization": api_key},
            params={"dates[]": fetch_date, "per_page": 30},
            timeout=10,
        )
        resp.raise_for_status()
        all_games.extend([g for g in resp.json()["data"] if g["status"] == "Final"])

    # deduplicate by game id
    seen, unique = set(), []
    for g in all_games:
        if g["id"] not in seen:
            seen.add(g["id"])
            unique.append(g)
    return unique


def _find_result(home_team: str, away_team: str, results: list[dict]) -> dict | None:
    for g in results:
        if (_names_match(g["home_team"]["full_name"], home_team) and
                _names_match(g["visitor_team"]["full_name"], away_team)):
            return g
    return None


def _names_match(a: str, b: str) -> bool:
    a_words = {w.lower() for w in a.split() if len(w) > 3}
    b_words = {w.lower() for w in b.split() if len(w) > 3}
    return bool(a_words & b_words)


def _fetch_player_stat(
    player_name: str, stat_key: str, game_id: int, api_key: str
) -> float | None:
    """Fetches a player's actual stat line for a specific game."""
    try:
        resp = requests.get(
            f"{BALLDONTLIE_BASE}/stats",
            headers={"Authorization": api_key},
            params={"game_ids[]": game_id, "per_page": 50},
            timeout=10,
        )
        resp.raise_for_status()
        stats = resp.json()["data"]
        name_lower = player_name.lower()
        for s in stats:
            full = f"{s['player']['first_name']} {s['player']['last_name']}".lower()
            if full == name_lower or name_lower.split()[-1] in full:
                val = s.get(stat_key)
                return float(val) if val is not None else None
    except Exception:
        pass
    return None


def _print_summary(picks: dict, date_str: str) -> None:
    s = picks["results_summary"]
    n = s["settled"]

    print(f"\n{'='*65}")
    print(f"  RESULTS — {date_str}  ({n} games settled)")
    print(f"{'='*65}")

    if n == 0:
        print("  No games settled yet.")
        return

    print(f"\n  Market accuracy:")
    print(f"    H2H:    {s['h2h_correct']}/{n}  ({s['h2h_accuracy']*100:.1f}%)")
    if s["spread_accuracy"] is not None:
        print(f"    Spread: {s['spread_correct']}/{n}  ({s['spread_accuracy']*100:.1f}%)")
    if s["total_accuracy"] is not None:
        print(f"    Totals: {s['total_correct']}/{n}  ({s['total_accuracy']*100:.1f}%)")

    print(f"\n  Game-by-game:")
    for pred in picks["predictions"]:
        if pred.get("h2h_actual_winner"):
            h = "✓" if pred["h2h_correct"]   else "✗"
            s_icon = ""
            t_icon = ""
            if pred.get("spread_correct") is not None:
                s_icon = f"  spread {'✓' if pred['spread_correct'] else '✗'} " \
                         f"(actual {pred['spread_actual_margin']:+.0f})"
            if pred.get("total_correct") is not None:
                t_icon = f"  total {'✓' if pred['total_correct'] else '✗'} " \
                         f"(actual {pred['total_actual']:.0f})"
            print(f"    {h} {pred['away_team']} @ {pred['home_team']}")
            print(f"      H2H: predicted {pred['h2h_predicted_winner']:20s} "
                  f"actual {pred['h2h_actual_winner']}")
            if s_icon: print(f"     {s_icon}")
            if t_icon: print(f"     {t_icon}")

    if picks["value_bets"]:
        print(f"\n  Value bets P&L:")
        won_count  = 0
        lost_count = 0
        for vb in picks["value_bets"]:
            if vb.get("outcome"):
                icon = "✓" if vb["outcome"] == "won" else "✗"
                market = vb["market"].upper()
                print(f"    {icon} [{market:6s}] {vb['bet_label']:30s} "
                      f"{vb['outcome'].upper():4s}  €{vb['actual_pnl']:+.2f}")
                if vb["outcome"] == "won": won_count += 1
                else: lost_count += 1

        sr = picks["results_summary"]
        print(f"\n    Record:       {won_count}W / {lost_count}L")
        print(f"    Total staked: €{sr['total_staked']:.2f}")
        print(f"    Total P&L:    €{sr['total_pnl']:+.2f}")
        roi = sr["total_pnl"] / sr["total_staked"] * 100 if sr["total_staked"] else 0
        print(f"    ROI:          {roi:+.1f}%")

    if picks.get("contrarian_picks"):
        correct = sum(1 for cp in picks["contrarian_picks"] if cp.get("correct"))
        total   = sum(1 for cp in picks["contrarian_picks"] if cp.get("correct") is not None)
        if total:
            print(f"\n  Contrarian picks: {correct}/{total} correct")
            for cp in picks["contrarian_picks"]:
                if cp.get("correct") is not None:
                    icon = "✓" if cp["correct"] else "✗"
                    print(f"    {icon}  {cp['game']}  →  model picked {cp['model_pick']}")

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(date_arg)
