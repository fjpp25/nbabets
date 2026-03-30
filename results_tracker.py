"""
results_tracker.py
Fetches last night's NBA results from balldontlie and updates the
picks JSON file with actual outcomes, P&L, and prediction accuracy.

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


# ── Main ──────────────────────────────────────────────────────────────────────
def run(date_str: str = None):
    load_dotenv()
    bdl_key = os.getenv("BALLDONTLIE_API_KEY")
    if not bdl_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    if not date_str:
        # default to yesterday in ET
        ET_OFFSET = timedelta(hours=-5)
        date_str  = (datetime.now(timezone.utc) + ET_OFFSET - timedelta(days=1)).date().isoformat()

    picks_path = DATA_DIR / f"picks_{date_str}.json"
    if not picks_path.exists():
        print(f"No picks file found for {date_str} ({picks_path})")
        return

    print(f"\nUpdating results for {date_str}...")

    with open(picks_path) as f:
        picks = json.load(f)

    # fetch actual results from balldontlie
    results = _fetch_results(date_str, bdl_key)
    if not results:
        print("  No completed games found yet — try again later.")
        return

    print(f"  {len(results)} completed game(s) found.")

    # update predictions
    pred_correct = 0
    pred_total   = 0
    for pred in picks["predictions"]:
        winner = _find_winner(pred["home_team"], pred["away_team"], results)
        if winner:
            pred["actual_winner"] = winner
            pred["correct"]       = (winner == pred["predicted_winner"])
            pred_total += 1
            if pred["correct"]:
                pred_correct += 1

    # update value bets
    total_staked = 0.0
    total_pnl    = 0.0
    for vb in picks["value_bets"]:
        # parse team names from "Away @ Home"
        parts     = vb["game"].split(" @ ")
        away_team = parts[0].strip()
        home_team = parts[1].strip()
        winner    = _find_winner(home_team, away_team, results)
        if winner:
            won            = (winner == vb["bet_team"])
            vb["outcome"]  = "won" if won else "lost"
            vb["actual_pnl"] = round(vb["simulated_profit"] if won else -vb["simulated_stake"], 2)
            total_staked  += vb["simulated_stake"]
            total_pnl     += vb["actual_pnl"]

    # update contrarian picks
    for cp in picks.get("contrarian_picks", []):
        parts     = cp["game"].split(" @ ")
        away_team = parts[0].strip()
        home_team = parts[1].strip()
        winner    = _find_winner(home_team, away_team, results)
        if winner:
            cp["correct"] = (winner == cp["model_pick"])

    # save summary
    picks["results_summary"] = {
        "prediction_accuracy": round(pred_correct / pred_total, 4) if pred_total else None,
        "predictions_correct": pred_correct,
        "predictions_total":   pred_total,
        "value_bets_total":    len(picks["value_bets"]),
        "value_bets_won":      sum(1 for vb in picks["value_bets"] if vb.get("outcome") == "won"),
        "total_staked":        round(total_staked, 2),
        "total_pnl":           round(total_pnl, 2),
        "updated_at":          datetime.now(timezone.utc).isoformat(),
    }

    with open(picks_path, "w") as f:
        json.dump(picks, f, indent=2)

    _print_summary(picks, date_str)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fetch_results(date_str: str, api_key: str) -> list[dict]:
    """Fetches all Final games for a given date from balldontlie."""
    resp = requests.get(
        f"{BALLDONTLIE_BASE}/games",
        headers={"Authorization": api_key},
        params={
            "dates[]":  date_str,
            "per_page": 30,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return [g for g in resp.json()["data"] if g["status"] == "Final"]


def _find_winner(home_team: str, away_team: str, results: list[dict]) -> str | None:
    """Matches a game from picks to a result and returns the winner's name."""
    for g in results:
        home_match = _names_match(g["home_team"]["full_name"], home_team)
        away_match = _names_match(g["visitor_team"]["full_name"], away_team)
        if home_match and away_match:
            if g["home_team_score"] > g["visitor_team_score"]:
                return g["home_team"]["full_name"]
            else:
                return g["visitor_team"]["full_name"]
    return None


def _names_match(a: str, b: str) -> bool:
    a_words = {w.lower() for w in a.split() if len(w) > 3}
    b_words = {w.lower() for w in b.split() if len(w) > 3}
    return bool(a_words & b_words)


def _print_summary(picks: dict, date_str: str) -> None:
    """Prints a readable results summary."""
    s = picks["results_summary"]
    print(f"\n{'='*65}")
    print(f"  RESULTS — {date_str}")
    print(f"{'='*65}")

    print(f"\n  Prediction accuracy: "
          f"{s['predictions_correct']}/{s['predictions_total']} "
          f"({s['prediction_accuracy']*100:.1f}%)" if s['prediction_accuracy'] else "  No predictions resolved.")

    print(f"\n  Game-by-game:")
    for pred in picks["predictions"]:
        if pred["actual_winner"]:
            icon = "✓" if pred["correct"] else "✗"
            print(f"    {icon}  {pred['away_team']} @ {pred['home_team']}")
            print(f"         Predicted: {pred['predicted_winner']:25s} | Actual: {pred['actual_winner']}")

    if picks["value_bets"]:
        print(f"\n  Value bets P&L:")
        for vb in picks["value_bets"]:
            if vb.get("outcome"):
                icon = "✓" if vb["outcome"] == "won" else "✗"
                print(f"    {icon}  {vb['bet_team']:25s}  {vb['outcome'].upper():4s}  "
                      f"€{vb['actual_pnl']:+.2f}")
        print(f"\n  Total staked:  €{s['total_staked']:.2f}")
        print(f"  Total P&L:     €{s['total_pnl']:+.2f}")
    else:
        print("\n  No value bets to settle today.")

    if picks.get("contrarian_picks"):
        correct = sum(1 for cp in picks["contrarian_picks"] if cp.get("correct"))
        total   = sum(1 for cp in picks["contrarian_picks"] if cp.get("correct") is not None)
        print(f"\n  Contrarian picks: {correct}/{total} correct")
        for cp in picks["contrarian_picks"]:
            if cp.get("correct") is not None:
                icon = "✓" if cp["correct"] else "✗"
                print(f"    {icon}  {cp['game']}  →  model picked {cp['model_pick']}")

    print(f"\n{'='*65}\n")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(date_arg)
