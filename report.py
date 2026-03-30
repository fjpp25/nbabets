"""
report.py
Aggregates all picks files in the data/ directory and produces a
summary report of the dry run's overall performance.

Usage:
    python report.py
"""

import json
from pathlib import Path


DATA_DIR = Path("data")


def run():
    picks_files = sorted(DATA_DIR.glob("picks_*.json"))
    if not picks_files:
        print("No picks files found in data/. Run dry_run.py first.")
        return

    print(f"\n{'='*65}")
    print(f"  DRY RUN REPORT — {len(picks_files)} day(s) of data")
    print(f"{'='*65}\n")

    # ── Aggregate stats ───────────────────────────────────────────────────────
    total_predictions  = 0
    total_correct      = 0
    total_value_bets   = 0
    total_value_won    = 0
    total_staked       = 0.0
    total_pnl          = 0.0
    daily_rows         = []
    all_value_bets     = []
    contrarian_correct = 0
    contrarian_total   = 0

    for path in picks_files:
        with open(path) as f:
            picks = json.load(f)

        s = picks.get("results_summary")
        date = picks["date"]

        if s:
            total_predictions += s["predictions_total"]
            total_correct     += s["predictions_correct"]
            total_value_bets  += s["value_bets_total"]
            total_value_won   += s["value_bets_won"]
            total_staked      += s["total_staked"]
            total_pnl         += s["total_pnl"]
            daily_rows.append({
                "date":     date,
                "acc":      s["prediction_accuracy"],
                "correct":  s["predictions_correct"],
                "total":    s["predictions_total"],
                "vb_won":   s["value_bets_won"],
                "vb_total": s["value_bets_total"],
                "pnl":      s["total_pnl"],
            })
        else:
            daily_rows.append({
                "date": date, "acc": None, "correct": 0,
                "total": 0, "vb_won": 0, "vb_total": 0, "pnl": 0.0,
            })

        for vb in picks.get("value_bets", []):
            all_value_bets.append({**vb, "date": date})

        for cp in picks.get("contrarian_picks", []):
            if cp.get("correct") is not None:
                contrarian_total  += 1
                contrarian_correct += int(cp["correct"])

    # ── Daily breakdown ───────────────────────────────────────────────────────
    print(f"  {'DATE':<12} {'ACC':>6}  {'CORRECT':>7}  {'VALUE BETS':>10}  {'DAY P&L':>9}")
    print(f"  {'─'*12} {'─'*6}  {'─'*7}  {'─'*10}  {'─'*9}")
    for r in daily_rows:
        acc_str = f"{r['acc']*100:.1f}%" if r["acc"] is not None else "pending"
        vb_str  = f"{r['vb_won']}/{r['vb_total']}" if r["vb_total"] > 0 else "none"
        pnl_str = f"€{r['pnl']:+.2f}" if r["vb_total"] > 0 else "—"
        print(f"  {r['date']:<12} {acc_str:>6}  "
              f"{r['correct']:>3}/{r['total']:<3}  "
              f"{vb_str:>10}  {pnl_str:>9}")

    # ── Overall summary ───────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    overall_acc = total_correct / total_predictions if total_predictions else 0
    print(f"  Overall prediction accuracy:  "
          f"{total_correct}/{total_predictions} ({overall_acc*100:.1f}%)")

    if total_value_bets > 0:
        vb_acc = total_value_won / total_value_bets
        roi    = total_pnl / total_staked * 100 if total_staked else 0
        print(f"  Value bet record:             "
              f"{total_value_won}/{total_value_bets} ({vb_acc*100:.1f}%)")
        print(f"  Total simulated staked:       €{total_staked:.2f}")
        print(f"  Total simulated P&L:          €{total_pnl:+.2f}")
        print(f"  ROI:                          {roi:+.1f}%")
    else:
        print("  No value bets recorded yet.")

    if contrarian_total > 0:
        c_acc = contrarian_correct / contrarian_total
        print(f"  Contrarian pick accuracy:     "
              f"{contrarian_correct}/{contrarian_total} ({c_acc*100:.1f}%)")

    # ── Value bet log ─────────────────────────────────────────────────────────
    if all_value_bets:
        print(f"\n  Value bet log:")
        print(f"  {'DATE':<12} {'BET':<28} {'ODDS':>5}  {'EDGE':>6}  {'OUTCOME':>7}  {'P&L':>7}")
        print(f"  {'─'*12} {'─'*28} {'─'*5}  {'─'*6}  {'─'*7}  {'─'*7}")
        for vb in all_value_bets:
            outcome = vb.get("outcome", "pending").upper()
            pnl     = f"€{vb['actual_pnl']:+.2f}" if vb.get("actual_pnl") is not None else "—"
            edge    = f"{vb['edge']*100:+.1f}%"
            bet_str = f"{vb['bet_team']}"[:27]
            print(f"  {vb['date']:<12} {bet_str:<28} {vb['best_odds']:>5.2f}  "
                  f"{edge:>6}  {outcome:>7}  {pnl:>7}")

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    run()
