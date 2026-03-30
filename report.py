"""
report.py
Aggregates all picks files and produces a cumulative dry run report
covering H2H, spread, and totals accuracy plus overall P&L.

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

    # ── Aggregate ─────────────────────────────────────────────────────────────
    totals = {
        "settled": 0,
        "h2h_correct": 0, "spread_correct": 0, "total_correct": 0,
        "vb_total": 0, "vb_won": 0,
        "staked": 0.0, "pnl": 0.0,
        "contrarian_correct": 0, "contrarian_total": 0,
    }
    daily_rows   = []
    all_vb       = []

    for path in picks_files:
        with open(path) as f:
            picks = json.load(f)

        s    = picks.get("results_summary", {})
        date = picks["date"]

        if s:
            n = s.get("settled", 0)
            totals["settled"]        += n
            totals["h2h_correct"]    += s.get("h2h_correct", 0)
            totals["spread_correct"] += s.get("spread_correct", 0)
            totals["total_correct"]  += s.get("total_correct", 0)
            totals["vb_total"]       += s.get("value_bets_total", 0)
            totals["vb_won"]         += s.get("value_bets_won", 0)
            totals["staked"]         += s.get("total_staked", 0.0)
            totals["pnl"]            += s.get("total_pnl", 0.0)

            for cp in picks.get("contrarian_picks", []):
                if cp.get("correct") is not None:
                    totals["contrarian_total"]   += 1
                    totals["contrarian_correct"] += int(cp["correct"])

            daily_rows.append({
                "date":    date,
                "settled": n,
                "h2h":     s.get("h2h_accuracy"),
                "spread":  s.get("spread_accuracy"),
                "totals":  s.get("total_accuracy"),
                "vb":      f"{s.get('value_bets_won',0)}/{s.get('value_bets_total',0)}",
                "pnl":     s.get("total_pnl", 0.0),
            })
        else:
            daily_rows.append({
                "date": date, "settled": 0,
                "h2h": None, "spread": None, "totals": None,
                "vb": "0/0", "pnl": 0.0,
            })

        for vb in picks.get("value_bets", []):
            all_vb.append({**vb, "date": date})

    # ── Daily breakdown ───────────────────────────────────────────────────────
    print(f"  {'DATE':<12} {'GAMES':>5}  {'H2H':>6}  {'SPREAD':>6}  "
          f"{'TOTALS':>6}  {'VB':>7}  {'P&L':>8}")
    print(f"  {'─'*12} {'─'*5}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*8}")

    for r in daily_rows:
        def fmt(v): return f"{v*100:.1f}%" if v is not None else "pending"
        pnl_str = f"€{r['pnl']:+.2f}" if r["settled"] > 0 else "—"
        print(f"  {r['date']:<12} {r['settled']:>5}  "
              f"{fmt(r['h2h']):>6}  {fmt(r['spread']):>6}  "
              f"{fmt(r['totals']):>6}  {r['vb']:>7}  {pnl_str:>8}")

    # ── Overall ───────────────────────────────────────────────────────────────
    n = totals["settled"]
    print(f"\n{'─'*65}")
    print(f"  Overall ({n} games settled across {len(picks_files)} day(s)):")

    def acc(correct, total):
        return f"{correct}/{total} ({correct/total*100:.1f}%)" if total else "—"

    print(f"    H2H accuracy:    {acc(totals['h2h_correct'],    n)}")
    print(f"    Spread accuracy: {acc(totals['spread_correct'], n)}")
    print(f"    Totals accuracy: {acc(totals['total_correct'],  n)}")

    if totals["vb_total"] > 0:
        roi = totals["pnl"] / totals["staked"] * 100 if totals["staked"] else 0
        print(f"\n    Value bet record: {acc(totals['vb_won'], totals['vb_total'])}")
        print(f"    Total staked:     €{totals['staked']:.2f}")
        print(f"    Total P&L:        €{totals['pnl']:+.2f}")
        print(f"    ROI:              {roi:+.1f}%")
    else:
        print(f"\n    No value bets settled yet.")

    if totals["contrarian_total"] > 0:
        print(f"\n    Contrarian picks: "
              f"{acc(totals['contrarian_correct'], totals['contrarian_total'])}")

    # ── Value bet log ─────────────────────────────────────────────────────────
    settled_vb = [vb for vb in all_vb if vb.get("outcome")]
    if settled_vb:
        print(f"\n  Value bet log ({len(settled_vb)} settled):")
        print(f"  {'DATE':<12} {'MKT':>6}  {'BET':<28} {'ODDS':>5}  "
              f"{'OUTCOME':>7}  {'P&L':>7}")
        print(f"  {'─'*12} {'─'*6}  {'─'*28} {'─'*5}  {'─'*7}  {'─'*7}")
        for vb in settled_vb:
            market  = vb["market"].upper()
            outcome = vb["outcome"].upper()
            pnl     = f"€{vb['actual_pnl']:+.2f}"
            label   = vb["bet_label"][:27]
            print(f"  {vb['date']:<12} {market:>6}  {label:<28} "
                  f"{vb['best_odds']:>5.2f}  {outcome:>7}  {pnl:>7}")

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    run()
