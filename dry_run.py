"""
dry_run.py
Daily entry point for the NBA betting dry run.
Covers H2H, spread, and totals markets.

Usage:
    python dry_run.py
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from odds_fetcher   import fetch_todays_games
from model          import load_models, predict_all_games, resolve_predictions
from value_detector import (find_value_bets, summarise_value_bets,
                             find_contrarian_picks, summarise_contrarian_picks)


DATA_DIR = Path("data")


def run():
    load_dotenv()
    odds_key = os.getenv("ODDS_API_KEY")
    bdl_key  = os.getenv("BALLDONTLIE_API_KEY")
    if not odds_key or not bdl_key:
        raise ValueError("Missing API keys — check your .env file")

    today    = datetime.now(timezone.utc).date()
    date_str = today.isoformat()
    DATA_DIR.mkdir(exist_ok=True)

    print(f"\n{'='*65}")
    print(f"  NBA DRY RUN — {date_str}")
    print(f"{'='*65}\n")

    # ── Step 1: Today's games + odds ──────────────────────────────────────────
    print("Step 1: Fetching today's games and odds...")
    games = fetch_todays_games(odds_key)

    if not games:
        print("  No NBA games today.\n")
        _save_picks(date_str, [], [], [], [])
        return

    print(f"  {len(games)} game(s) found:\n")
    for g in games:
        print(f"    {g['away_team']:25s} @ {g['home_team']}")

    # ── Step 2: Model predictions ─────────────────────────────────────────────
    print(f"\nStep 2: Running model predictions...")
    models      = load_models()
    predictions = predict_all_games(games, bdl_key, models)
    predictions = resolve_predictions(predictions)

    # ── Step 3: Value detection ───────────────────────────────────────────────
    print(f"\nStep 3: Detecting value bets and contrarian picks...")
    value_bets  = find_value_bets(predictions, games)
    contrarians = find_contrarian_picks(predictions, games)

    # ── Step 4: Save picks ────────────────────────────────────────────────────
    picks_path = _save_picks(date_str, games, predictions, value_bets, contrarians)
    print(f"\nPicks saved to {picks_path}")

    # ── Step 5: Print summary ─────────────────────────────────────────────────
    print("\n--- All predictions ---")
    _print_predictions(predictions)

    print("--- Value bets ---")
    summarise_value_bets(value_bets)

    print("--- Contrarian picks ---")
    summarise_contrarian_picks(contrarians)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _print_predictions(predictions: list[dict]) -> None:
    print(f"\n  {'MATCHUP':<40} {'WINNER':22} {'PROB':>5}  "
          f"{'MARGIN':>7}  {'TOTAL':>6}  {'O/U':>5}  CONF")
    print(f"  {'─'*40} {'─'*22} {'─'*5}  {'─'*7}  {'─'*6}  {'─'*5}  {'─'*5}")

    for p in predictions:
        matchup  = f"{p['away_team']} @ {p['home_team']}"[:39]
        winner   = p["h2h"]["predicted_winner"][:21]
        prob     = max(p["h2h"]["home_win_prob"], p["h2h"]["away_win_prob"])
        margin   = p["spread"]["predicted_margin"]
        total    = p["totals"]["predicted_total"]
        ou       = p["totals"].get("prediction") or "—"
        conf     = p["h2h"]["confidence"].upper()
        print(f"  {matchup:<40} {winner:<22} {prob:>5.1%}  "
              f"{margin:>+7.1f}  {total:>6.1f}  {ou:>5}  {conf}")
    print()


def _save_picks(
    date_str:    str,
    games:       list[dict],
    predictions: list[dict],
    value_bets:  list[dict],
    contrarians: list[dict],
) -> Path:
    picks = {
        "date":          date_str,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "games_today":   len(games),
        "predictions": [
            {
                "home_team":      p["home_team"],
                "away_team":      p["away_team"],
                # H2H
                "h2h_predicted_winner": p["h2h"]["predicted_winner"],
                "h2h_home_prob":        p["h2h"]["home_win_prob"],
                "h2h_away_prob":        p["h2h"]["away_win_prob"],
                "h2h_confidence":       p["h2h"]["confidence"],
                "h2h_actual_winner":    None,
                "h2h_correct":          None,
                # Spread
                "spread_predicted_margin": p["spread"]["predicted_margin"],
                "spread_book_line":        p["spread"]["book_spread"],
                "spread_covers":           p["spread"]["covers_spread"],
                "spread_actual_margin":    None,
                "spread_covered":          None,
                # Totals
                "total_predicted":         p["totals"]["predicted_total"],
                "total_book_line":         p["totals"]["book_total"],
                "total_prediction":        p["totals"]["prediction"],
                "total_margin":            p["totals"]["margin"],
                "total_actual":            None,
                "total_correct":           None,
            }
            for p in predictions
        ],
        "value_bets":  value_bets,
        "contrarian_picks": [
            {**c, "correct": None}
            for c in contrarians
        ],
    }

    path = DATA_DIR / f"picks_{date_str}.json"
    with open(path, "w") as f:
        json.dump(picks, f, indent=2)
    return path


if __name__ == "__main__":
    run()
