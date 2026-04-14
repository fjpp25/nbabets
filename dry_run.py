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
from value_detector  import (find_value_bets, summarise_value_bets,
                              find_contrarian_picks, summarise_contrarian_picks)
from player_props    import fetch_player_props, find_prop_value_bets, summarise_prop_bets
from injuries        import get_injury_report


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
    games = fetch_todays_games(odds_key, bdl_api_key=bdl_key)

    if not games:
        print("  No NBA games today.\n")
        _save_picks(date_str, [], [], [], [])
        return

    print(f"  {len(games)} game(s) found:\n")
    for g in games:
        print(f"    {g['away_team']:25s} @ {g['home_team']}")

    # ── Step 2: Model predictions ─────────────────────────────────────────────
    print(f"\nStep 2: Running model predictions...")
    print("  Pre-fetching injury report for props filter...", end=" ", flush=True)
    try:
        _injury_report = get_injury_report(bdl_key)
        print(f"{len(_injury_report)} players listed")
    except Exception:
        _injury_report = []
        print("failed — continuing without")
    models      = load_models()
    predictions = predict_all_games(games, bdl_key, models, injury_report=_injury_report)
    predictions = resolve_predictions(predictions)

    # ── Step 3: Value detection ───────────────────────────────────────────────
    print(f"\nStep 3: Detecting value bets and contrarian picks...")
    value_bets  = find_value_bets(predictions, games)
    contrarians = find_contrarian_picks(predictions, games)

    # ── Step 3b: Player props ─────────────────────────────────────────────────────
    print(f"\nStep 3b: Fetching and evaluating player props...")
    props     = fetch_player_props(odds_key, games)
    prop_bets = find_prop_value_bets(props, bdl_key, injury_report=_injury_report)

    # ── Step 4: Save picks ────────────────────────────────────────────────────
    picks_path = _save_picks(date_str, games, predictions, value_bets, contrarians, prop_bets)
    print(f"\nPicks saved to {picks_path}")

    # ── Step 5: Print summary ─────────────────────────────────────────────────
    print("\n--- All predictions ---")
    _print_predictions(predictions)

    print("--- Value bets ---")
    summarise_value_bets(value_bets)

    print("--- Contrarian picks ---")
    summarise_contrarian_picks(contrarians)

    print("--- Player props ---")
    summarise_prop_bets(prop_bets)


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
        b2b      = p["h2h"].get("b2b_flag", {})
        b2b_str  = ""
        if b2b.get("home_b2b"): b2b_str = f" B2B({p['home_team'].split()[-1]})"
        if b2b.get("away_b2b"): b2b_str = f" B2B({p['away_team'].split()[-1]})"
        inj_flag = (" ⚠" if p.get("injury_affected") else "") + b2b_str
        print(f"  {matchup:<40} {winner:<22} {prob:>5.1%}  "
              f"{margin:>+7.1f}  {total:>6.1f}  {ou:>5}  {conf}{inj_flag}")

    # Print injury details for affected games
    affected = [p for p in predictions if p.get("injury_affected")]
    if affected:
        print(f"\n  ⚠ INJURY ALERTS:")
        for p in affected:
            s = p.get("injury_summary", {})
            print(f"\n  {p['away_team']} @ {p['home_team']}")
            # Home injuries
            for inj in s.get("home_injuries", []):
                raw  = p["h2h"].get("home_win_prob_raw", p["h2h"]["home_win_prob"])
                adj  = p["h2h"]["home_win_prob"]
                tier = inj["tier"].upper()
                print(f"    [{tier}] {inj['player_name']:25s} {inj['status']:12s} "
                      f"({inj['ppg']:.1f} ppg)  adj: {inj['adjustment']:+.1%}")
            # Away injuries
            for inj in s.get("away_injuries", []):
                tier = inj["tier"].upper()
                print(f"    [{tier}] {inj['player_name']:25s} {inj['status']:12s} "
                      f"({inj['ppg']:.1f} ppg)  adj: {inj['adjustment']:+.1%}")
            # Show prob shift
            home_raw = p["h2h"].get("home_win_prob_raw")
            if home_raw:
                home_adj = p["h2h"]["home_win_prob"]
                away_raw = p["h2h"].get("away_win_prob_raw", 1 - home_raw)
                away_adj = p["h2h"]["away_win_prob"]
                print(f"    Prob shift: {p['home_team']} {home_raw:.1%} → {home_adj:.1%}  |  "
                      f"{p['away_team']} {away_raw:.1%} → {away_adj:.1%}")
    print()


def _save_picks(
    date_str:    str,
    games:       list[dict],
    predictions: list[dict],
    value_bets:  list[dict],
    contrarians: list[dict],
    prop_bets:   list[dict] = None,
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
                "injury_affected":      p.get("injury_affected", False),
                "injury_summary":       p.get("injury_summary", {}),
                "h2h_home_prob_raw":    p["h2h"].get("home_win_prob_raw"),
                "h2h_away_prob_raw":    p["h2h"].get("away_win_prob_raw"),
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
        "value_bets":  [
            {**vb, "opening_odds": vb.get("best_odds")}
            for vb in value_bets
        ],
        "contrarian_picks": [
            {**c, "correct": None}
            for c in contrarians
        ],
        "prop_bets": prop_bets or [],
    }

    path = DATA_DIR / f"picks_{date_str}.json"
    with open(path, "w") as f:
        json.dump(picks, f, indent=2)
    return path


if __name__ == "__main__":
    run()
