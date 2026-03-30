"""
dry_run.py
Daily entry point for the NBA betting dry run.

What it does each day:
  1. Fetches today's NBA games + bookmaker odds (The Odds API)
  2. Runs model predictions for each matchup (balldontlie + trained model)
  3. Identifies value bets (model edge vs bookmaker implied probability)
  4. Saves the day's picks to data/picks_YYYY-MM-DD.json
  5. Prints a summary to the console

Run once per day, ideally a few hours before games tip off.

Usage:
    python dry_run.py
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from odds_fetcher import fetch_todays_games
from nba_stats import get_team_stats
from model import load_model, predict_all_games
from value_detector import find_value_bets, summarise_value_bets, find_contrarian_picks, summarise_contrarian_picks


# ── Constants ─────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")


# ── Main pipeline ─────────────────────────────────────────────────────────────
def run():
    load_dotenv()
    odds_key = os.getenv("ODDS_API_KEY")
    bdl_key  = os.getenv("BALLDONTLIE_API_KEY")

    if not odds_key or not bdl_key:
        raise ValueError("Missing API keys — check your .env file")

    today     = datetime.now(timezone.utc).date()
    date_str  = today.isoformat()
    DATA_DIR.mkdir(exist_ok=True)

    print(f"\n{'='*65}")
    print(f"  NBA DRY RUN — {date_str}")
    print(f"{'='*65}\n")

    # ── Step 1: Today's games + odds ──────────────────────────────────────────
    print("Step 1: Fetching today's games and odds...")
    games = fetch_todays_games(odds_key)

    if not games:
        print("  No NBA games today. Nothing to do.\n")
        _save_picks(date_str, [], [], [])
        return

    print(f"  {len(games)} game(s) found:\n")
    for g in games:
        print(f"    {g['away_team']:25s} @ {g['home_team']}")

    # ── Step 2: Model predictions ─────────────────────────────────────────────
    print(f"\nStep 2: Running model predictions...")
    model_payload = load_model()
    predictions   = predict_all_games(games, bdl_key, model_payload)

    # ── Step 3: Value detection ───────────────────────────────────────────────
    print(f"\nStep 3: Detecting value bets (min edge: 5%)...")
    value_bets = find_value_bets(predictions, games)
    contrarians = find_contrarian_picks(predictions, games)

    # ── Step 4: Save picks ────────────────────────────────────────────────────
    picks_path = _save_picks(date_str, games, predictions, value_bets, contrarians)
    print(f"\nPicks saved to {picks_path}")

    # ── Step 5: Print summary ─────────────────────────────────────────────────
    print("\n--- All predictions ---")
    _print_predictions(predictions)
    summarise_value_bets(value_bets)
    print("--- Contrarian picks ---")
    summarise_contrarian_picks(contrarians)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _print_predictions(predictions: list[dict]) -> None:
    """Prints a concise prediction table for all games."""
    print(f"\n  {'MATCHUP':<45} {'PREDICTED WINNER':<25} {'PROB':>6}  {'CONF'}")
    print(f"  {'─'*45} {'─'*25} {'─'*6}  {'─'*6}")
    for p in predictions:
        matchup  = f"{p['away_team']} @ {p['home_team']}"
        winner   = p["predicted_winner"]
        prob     = max(p["home_win_prob"], p["away_win_prob"])
        conf     = p["confidence"].upper()
        print(f"  {matchup:<45} {winner:<25} {prob:>6.1%}  {conf}")
    print()


def _save_picks(
    date_str:    str,
    games:       list[dict],
    predictions: list[dict],
    value_bets:  list[dict],
    contrarians: list[dict] = None,
) -> Path:
    """
    Saves today's picks to data/picks_YYYY-MM-DD.json.

    The JSON structure is designed to be easy to update the next day
    with actual outcomes via results_tracker.py.
    """
    picks = {
        "date":        date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "games_today": len(games),
        "predictions": [
            {
                "home_team":        p["home_team"],
                "away_team":        p["away_team"],
                "home_win_prob":    p["home_win_prob"],
                "away_win_prob":    p["away_win_prob"],
                "predicted_winner": p["predicted_winner"],
                "confidence":       p["confidence"],
                "actual_winner":    None,    # filled in by results_tracker.py
                "correct":          None,    # filled in by results_tracker.py
            }
            for p in predictions
        ],
        "contrarian_picks": [
            {
                "game":           c["game"],
                "book_favourite": c["book_favourite"],
                "book_prob":      c["book_prob"],
                "model_pick":     c["model_pick"],
                "model_prob":     c["model_prob"],
                "confidence":     c["confidence"],
                "correct":        None,   # filled in by results_tracker.py
            }
            for c in (contrarians or [])
        ],
        "value_bets": [
            {
                "game":            vb["game"],
                "commence_time":   vb["commence_time"],
                "bet_team":        vb["bet_team"],
                "model_prob":      vb["model_prob"],
                "implied_prob":    vb["implied_prob"],
                "edge":            vb["edge"],
                "best_odds":       vb["best_odds"],
                "bookmaker":       vb["bookmaker"],
                "simulated_stake": vb["simulated_stake"],
                "simulated_return": vb["simulated_return"],
                "simulated_profit": vb["simulated_profit"],
                "confidence":      vb["confidence"],
                "outcome":         None,     # "won" / "lost" — filled in by results_tracker.py
                "actual_pnl":      None,     # filled in by results_tracker.py
            }
            for vb in value_bets
        ],
    }

    path = DATA_DIR / f"picks_{date_str}.json"
    with open(path, "w") as f:
        json.dump(picks, f, indent=2)

    return path


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run()
