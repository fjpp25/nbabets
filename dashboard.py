"""
dashboard.py
Reads all picks JSON files from data/ and generates a self-contained
HTML dashboard at dashboard.html.

Usage:
    python dashboard.py
    python dashboard.py --from 2026-03-30 --to 2026-04-01
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
OUT_FILE = Path("dashboard.html")


# ── Data loading ──────────────────────────────────────────────────────────────
def load_picks(from_date=None, to_date=None) -> list[dict]:
    files = sorted(DATA_DIR.glob("picks_*.json"))
    picks = []
    for f in files:
        date_str = f.stem.replace("picks_", "")
        if from_date and date_str < from_date: continue
        if to_date   and date_str > to_date:   continue
        with open(f) as fp:
            data = json.load(fp)
        if data.get("results_summary"):
            picks.append(data)
    return picks


def aggregate(picks: list[dict]) -> dict:
    days = []
    all_vb, all_props = [], []
    totals = dict(
        games=0,
        h2h_correct=0, h2h_total=0,
        spread_correct=0, spread_total=0,
        total_correct=0, total_total=0,
        vb_won=0, vb_total=0, vb_staked=0.0, vb_pnl=0.0,
        prop_won=0, prop_total=0, prop_staked=0.0, prop_pnl=0.0,
        contrarian_correct=0, contrarian_total=0,
    )
    conf_stats   = {c: {"won":0,"total":0} for c in ["HIGH","MEDIUM","LOW"]}
    risk_buckets = {"0-20":{"won":0,"total":0},"21-40":{"won":0,"total":0},
                    "41-60":{"won":0,"total":0},"61-100":{"won":0,"total":0}}
    prop_by_stat = {"pts":{"won":0,"total":0},"reb":{"won":0,"total":0},"ast":{"won":0,"total":0}}
    cumulative_pnl = 0.0
    running = []

    for p in picks:
        s   = p["results_summary"]
        n   = s.get("settled", 0)
        date = p["date"]
        day_pnl = s.get("total_pnl", 0.0)
        cumulative_pnl += day_pnl
        running.append({"date": date, "pnl": round(day_pnl, 2),
                         "cumulative": round(cumulative_pnl, 2)})

        totals["games"]          += n
        totals["h2h_correct"]    += s.get("h2h_correct", 0)
        totals["h2h_total"]      += n
        totals["spread_correct"] += s.get("spread_correct", 0)
        totals["spread_total"]   += n
        totals["total_correct"]  += s.get("total_correct", 0)
        totals["total_total"]    += n
        totals["vb_won"]         += s.get("value_bets_won", 0)
        totals["vb_total"]       += s.get("value_bets_total", 0)
        totals["vb_staked"]      += s.get("value_bets_staked", s.get("total_staked", 0.0))
        totals["vb_pnl"]         += s.get("value_bets_pnl", s.get("total_pnl", 0.0))
        totals["prop_won"]       += s.get("prop_bets_won", 0)
        totals["prop_total"]     += s.get("prop_bets_total", 0)
        totals["prop_staked"]    += s.get("prop_bets_staked", 0.0)
        totals["prop_pnl"]       += s.get("prop_bets_pnl", 0.0)

        for cp in p.get("contrarian_picks", []):
            if cp.get("correct") is not None:
                totals["contrarian_total"]   += 1
                totals["contrarian_correct"] += int(cp["correct"])

        # value bets — confidence + risk breakdown
        for vb in p.get("value_bets", []):
            if vb.get("outcome") is None: continue
            won  = vb["outcome"] == "won"
            conf = vb.get("confidence", "LOW").upper()
            if conf in conf_stats:
                conf_stats[conf]["total"] += 1
                if won: conf_stats[conf]["won"] += 1
            rs = vb.get("risk_score")
            if rs is not None:
                bkt = "0-20" if rs<=20 else "21-40" if rs<=40 else "41-60" if rs<=60 else "61-100"
                risk_buckets[bkt]["total"] += 1
                if won: risk_buckets[bkt]["won"] += 1
            all_vb.append({**vb, "date": date})

        # props — stat type breakdown
        for pb in p.get("prop_bets", []):
            if pb.get("outcome") is None: continue
            won  = pb["outcome"] == "won"
            stat = pb.get("stat", "pts")
            if stat in prop_by_stat:
                prop_by_stat[stat]["total"] += 1
                if won: prop_by_stat[stat]["won"] += 1
            all_props.append({**pb, "date": date})

        days.append({
            "date":    date,
            "games":   n,
            "h2h":     round(s["h2h_correct"]/n*100,1) if n else 0,
            "spread":  round(s["spread_correct"]/n*100,1) if n else 0,
            "totals":  round(s["total_correct"]/n*100,1) if n else 0,
            "vb_rec":  f"{s.get('value_bets_won',0)}/{s.get('value_bets_total',0)}",
            "pnl":     round(day_pnl, 2),
        })

    # best/worst value bets
    settled_vb    = [v for v in all_vb if v.get("actual_pnl") is not None]
    best_bets     = sorted(settled_vb, key=lambda x: x["actual_pnl"], reverse=True)[:5]
    worst_bets    = sorted(settled_vb, key=lambda x: x["actual_pnl"])[:5]
    settled_props = [pb for pb in all_props if pb.get("actual_pnl") is not None]

    return {
        "days": days, "totals": totals, "running": running,
        "conf_stats": conf_stats, "risk_buckets": risk_buckets,
        "prop_by_stat": prop_by_stat,
        "best_bets": best_bets, "worst_bets": worst_bets,
        "settled_props": settled_props,
    }


# ── HTML generation ───────────────────────────────────────────────────────────
def pct(w, t): return f"{w/t*100:.1f}%" if t else "—"
def roi(pnl, staked): return f"{pnl/staked*100:+.1f}%" if staked else "—"

def build_html(data: dict, from_date=None, to_date=None) -> str:
    d  = data["days"]
    t  = data["totals"]
    r  = data["running"]
    cs = data["conf_stats"]
    rb = data["risk_buckets"]
    ps = data["prop_by_stat"]
    bb = data["best_bets"]
    wb = data["worst_bets"]

    # chart data
    chart_labels  = [row["date"] for row in r]
    chart_daily   = [row["pnl"]        for row in r]
    chart_cumul   = [row["cumulative"] for row in r]
    h2h_acc       = [row["h2h"]        for row in d]
    spread_acc    = [row["spread"]     for row in d]
    totals_acc    = [row["totals"]     for row in d]

    # summary cards
    vb_roi  = roi(t["vb_pnl"],   t["vb_staked"])
    all_pnl = t["vb_pnl"] + t["prop_pnl"]
    all_stk = t["vb_staked"] + t["prop_staked"]

    date_range = ""
    if from_date or to_date:
        date_range = f" ({from_date or '…'} → {to_date or '…'})"

    # daily table rows
    day_rows = ""
    for row in reversed(d):
        pnl_cls = "pos" if row["pnl"] > 0 else "neg" if row["pnl"] < 0 else ""
        day_rows += f"""
        <tr>
          <td>{row['date']}</td>
          <td>{row['games']}</td>
          <td>{row['h2h']}%</td>
          <td>{row['spread']}%</td>
          <td>{row['totals']}%</td>
          <td>{row['vb_rec']}</td>
          <td class="{pnl_cls}">€{row['pnl']:+.2f}</td>
        </tr>"""

    # best/worst rows
    def bet_row(b):
        cls = "pos" if b["actual_pnl"] > 0 else "neg"
        mkt = b.get("market","").upper()
        lbl = b.get("bet_label", b.get("player",""))[:28]
        return f"<tr><td>{b['date']}</td><td>{mkt}</td><td>{lbl}</td><td>{b.get('best_odds','—')}</td><td class='{cls}'>€{b['actual_pnl']:+.2f}</td></tr>"

    best_rows  = "".join(bet_row(b) for b in bb)
    worst_rows = "".join(bet_row(b) for b in wb)

    # confidence bars
    def conf_bar(label, stats, color):
        w = int(stats["won"]/stats["total"]*100) if stats["total"] else 0
        return f"""
        <div class="bar-row">
          <span class="bar-label">{label}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width:{w}%;background:{color}"></div>
          </div>
          <span class="bar-val">{pct(stats['won'],stats['total'])} ({stats['won']}/{stats['total']})</span>
        </div>"""

    conf_bars = (
        conf_bar("HIGH",   cs["HIGH"],   "#4ade80") +
        conf_bar("MEDIUM", cs["MEDIUM"], "#facc15") +
        conf_bar("LOW",    cs["LOW"],    "#f87171")
    )

    def risk_bar(label, stats):
        w   = int(stats["won"]/stats["total"]*100) if stats["total"] else 0
        col = "#4ade80" if w>=60 else "#facc15" if w>=45 else "#f87171"
        return f"""
        <div class="bar-row">
          <span class="bar-label">{label}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width:{w}%;background:{col}"></div>
          </div>
          <span class="bar-val">{pct(stats['won'],stats['total'])} ({stats['won']}/{stats['total']})</span>
        </div>"""

    risk_bars = "".join(risk_bar(k,v) for k,v in rb.items())

    def prop_bar(label, stat_key, emoji):
        s   = ps[stat_key]
        w   = int(s["won"]/s["total"]*100) if s["total"] else 0
        col = "#4ade80" if w>=60 else "#facc15" if w>=45 else "#f87171"
        return f"""
        <div class="bar-row">
          <span class="bar-label">{emoji} {label}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width:{w}%;background:{col}"></div>
          </div>
          <span class="bar-val">{pct(s['won'],s['total'])} ({s['won']}/{s['total']})</span>
        </div>"""

    prop_bars = (
        prop_bar("Points",   "pts", "🏀") +
        prop_bar("Rebounds", "reb", "💪") +
        prop_bar("Assists",  "ast", "🎯")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NBA Betting Dashboard{date_range}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:      #0a0e1a;
    --surface: #111827;
    --border:  #1e2d40;
    --accent:  #00d4ff;
    --accent2: #ff6b35;
    --pos:     #4ade80;
    --neg:     #f87171;
    --text:    #e2e8f0;
    --muted:   #64748b;
    --font-display: 'Syne', sans-serif;
    --font-mono:    'DM Mono', monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 13px;
    min-height: 100vh;
    padding: 32px 24px;
  }}
  .header {{
    display: flex;
    align-items: baseline;
    gap: 16px;
    margin-bottom: 40px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 20px;
  }}
  .header h1 {{
    font-family: var(--font-display);
    font-size: 32px;
    font-weight: 800;
    letter-spacing: -1px;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .header .sub {{
    color: var(--muted);
    font-size: 12px;
  }}
  .grid-4 {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 24px;
  }}
  .grid-2 {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 16px;
    margin-bottom: 24px;
  }}
  .grid-3 {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
    margin-bottom: 24px;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
  }}
  .card-title {{
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
  }}
  .stat-big {{
    font-family: var(--font-display);
    font-size: 36px;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 4px;
  }}
  .stat-sub {{
    font-size: 11px;
    color: var(--muted);
  }}
  .accent  {{ color: var(--accent);  }}
  .accent2 {{ color: var(--accent2); }}
  .pos     {{ color: var(--pos);     }}
  .neg     {{ color: var(--neg);     }}
  .muted   {{ color: var(--muted);   }}
  canvas   {{ max-height: 260px;     }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }}
  th {{
    text-align: left;
    color: var(--muted);
    font-weight: 500;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
  }}
  td {{
    padding: 8px 8px;
    border-bottom: 1px solid var(--border);
    font-family: var(--font-mono);
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: rgba(255,255,255,0.02); }}
  .market-grid {{
    display: grid;
    grid-template-columns: repeat(4,1fr);
    gap: 12px;
    margin-bottom: 24px;
  }}
  .market-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    text-align: center;
  }}
  .market-card .label {{
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 8px;
  }}
  .market-card .pct {{
    font-family: var(--font-display);
    font-size: 28px;
    font-weight: 800;
  }}
  .market-card .rec {{
    font-size: 11px;
    color: var(--muted);
    margin-top: 2px;
  }}
  .bar-row {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }}
  .bar-label {{
    width: 70px;
    font-size: 11px;
    color: var(--muted);
    flex-shrink: 0;
  }}
  .bar-track {{
    flex: 1;
    height: 8px;
    background: var(--border);
    border-radius: 4px;
    overflow: hidden;
  }}
  .bar-fill {{
    height: 100%;
    border-radius: 4px;
    transition: width 0.8s ease;
  }}
  .bar-val {{
    width: 110px;
    text-align: right;
    font-size: 11px;
    color: var(--text);
    flex-shrink: 0;
  }}
  .divider {{
    border: none;
    border-top: 1px solid var(--border);
    margin: 24px 0;
  }}
  @keyframes fadeUp {{
    from {{ opacity:0; transform: translateY(16px); }}
    to   {{ opacity:1; transform: translateY(0);    }}
  }}
  .card {{ animation: fadeUp 0.4s ease both; }}
  .card:nth-child(2) {{ animation-delay: 0.05s; }}
  .card:nth-child(3) {{ animation-delay: 0.10s; }}
  .card:nth-child(4) {{ animation-delay: 0.15s; }}
</style>
</head>
<body>

<div class="header">
  <h1>NBA BETTING DASHBOARD</h1>
  <span class="sub">dry run{date_range} · generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
</div>

<!-- Summary cards -->
<div class="grid-4">
  <div class="card">
    <div class="card-title">Games Settled</div>
    <div class="stat-big accent">{t['games']}</div>
    <div class="stat-sub">{len(d)} day(s) of data</div>
  </div>
  <div class="card">
    <div class="card-title">Value Bets</div>
    <div class="stat-big {'pos' if t['vb_pnl']>=0 else 'neg'}">€{t['vb_pnl']:+.2f}</div>
    <div class="stat-sub">{t['vb_won']}/{t['vb_total']} won · ROI {vb_roi}</div>
  </div>
  <div class="card">
    <div class="card-title">Player Props</div>
    <div class="stat-big {'pos' if t['prop_pnl']>=0 else 'neg'}">€{t['prop_pnl']:+.2f}</div>
    <div class="stat-sub">{t['prop_won']}/{t['prop_total']} won · ROI {roi(t['prop_pnl'],t['prop_staked'])}</div>
  </div>
  <div class="card">
    <div class="card-title">Combined P&L</div>
    <div class="stat-big {'pos' if all_pnl>=0 else 'neg'}">€{all_pnl:+.2f}</div>
    <div class="stat-sub">ROI {roi(all_pnl, all_stk)} on €{all_stk:.2f} staked</div>
  </div>
</div>

<!-- Market accuracy -->
<div class="market-grid">
  <div class="market-card">
    <div class="label">H2H</div>
    <div class="pct accent">{pct(t['h2h_correct'],t['h2h_total'])}</div>
    <div class="rec">{t['h2h_correct']}/{t['h2h_total']} correct</div>
  </div>
  <div class="market-card">
    <div class="label">Spread</div>
    <div class="pct">{pct(t['spread_correct'],t['spread_total'])}</div>
    <div class="rec">{t['spread_correct']}/{t['spread_total']} correct</div>
  </div>
  <div class="market-card">
    <div class="label">Totals</div>
    <div class="pct">{pct(t['total_correct'],t['total_total'])}</div>
    <div class="rec">{t['total_correct']}/{t['total_total']} correct</div>
  </div>
  <div class="market-card">
    <div class="label">Contrarian</div>
    <div class="pct accent2">{pct(t['contrarian_correct'],t['contrarian_total'])}</div>
    <div class="rec">{t['contrarian_correct']}/{t['contrarian_total']} correct</div>
  </div>
</div>

<!-- P&L Charts -->
<div class="grid-2">
  <div class="card">
    <div class="card-title">Cumulative P&L</div>
    <canvas id="cumulChart"></canvas>
  </div>
  <div class="card">
    <div class="card-title">Daily Accuracy by Market</div>
    <canvas id="accChart"></canvas>
  </div>
</div>

<!-- Confidence + Risk + Props -->
<div class="grid-3">
  <div class="card">
    <div class="card-title">Accuracy by Confidence</div>
    {conf_bars}
  </div>
  <div class="card">
    <div class="card-title">Accuracy by Risk Score</div>
    {risk_bars}
  </div>
  <div class="card">
    <div class="card-title">Prop Hit Rate by Stat</div>
    {prop_bars}
  </div>
</div>

<!-- Best/Worst + Daily table -->
<div class="grid-2">
  <div class="card">
    <div class="card-title">🏆 Best Bets</div>
    <table>
      <thead><tr><th>Date</th><th>Mkt</th><th>Bet</th><th>Odds</th><th>P&L</th></tr></thead>
      <tbody>{best_rows}</tbody>
    </table>
  </div>
  <div class="card">
    <div class="card-title">💀 Worst Bets</div>
    <table>
      <thead><tr><th>Date</th><th>Mkt</th><th>Bet</th><th>Odds</th><th>P&L</th></tr></thead>
      <tbody>{worst_rows}</tbody>
    </table>
  </div>
</div>

<!-- Daily breakdown -->
<div class="card">
  <div class="card-title">Day-by-Day Breakdown</div>
  <table>
    <thead>
      <tr><th>Date</th><th>Games</th><th>H2H%</th><th>Spread%</th><th>Totals%</th><th>VB Record</th><th>P&L</th></tr>
    </thead>
    <tbody>{day_rows}</tbody>
  </table>
</div>

<script>
const labels  = {json.dumps(chart_labels)};
const daily   = {json.dumps(chart_daily)};
const cumul   = {json.dumps(chart_cumul)};
const h2hAcc  = {json.dumps(h2h_acc)};
const sprAcc  = {json.dumps(spread_acc)};
const totAcc  = {json.dumps(totals_acc)};

const gridColor = 'rgba(255,255,255,0.05)';
const baseOpts  = {{
  responsive: true,
  plugins: {{ legend: {{ labels: {{ color:'#94a3b8', font:{{ family:'DM Mono', size:11 }} }} }} }},
  scales: {{
    x: {{ ticks:{{ color:'#64748b' }}, grid:{{ color:gridColor }} }},
    y: {{ ticks:{{ color:'#64748b' }}, grid:{{ color:gridColor }} }},
  }}
}};

// Cumulative P&L chart
new Chart(document.getElementById('cumulChart'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [
      {{
        label: 'Daily P&L',
        data: daily,
        borderColor: '#00d4ff',
        backgroundColor: 'rgba(0,212,255,0.08)',
        fill: true,
        tension: 0.3,
        pointRadius: 4,
      }},
      {{
        label: 'Cumulative',
        data: cumul,
        borderColor: '#ff6b35',
        backgroundColor: 'rgba(255,107,53,0.08)',
        fill: true,
        tension: 0.3,
        pointRadius: 4,
        borderDash: [4,3],
      }},
    ]
  }},
  options: {{
    ...baseOpts,
    plugins: {{
      ...baseOpts.plugins,
      tooltip: {{
        callbacks: {{
          label: ctx => ` €${{ctx.parsed.y >= 0 ? '+' : ''}}${{ctx.parsed.y.toFixed(2)}}`
        }}
      }}
    }}
  }}
}});

// Accuracy chart
new Chart(document.getElementById('accChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [
      {{ label: 'H2H%',    data: h2hAcc, backgroundColor: 'rgba(0,212,255,0.7)' }},
      {{ label: 'Spread%', data: sprAcc, backgroundColor: 'rgba(255,107,53,0.7)' }},
      {{ label: 'Totals%', data: totAcc, backgroundColor: 'rgba(250,204,21,0.7)' }},
    ]
  }},
  options: {{
    ...baseOpts,
    scales: {{
      ...baseOpts.scales,
      y: {{ ...baseOpts.scales.y, min:0, max:100 }}
    }}
  }}
}});
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default=None)
    parser.add_argument("--to",   dest="to_date",   default=None)
    args = parser.parse_args()

    picks = load_picks(args.from_date, args.to_date)
    if not picks:
        print("No settled picks found. Run results_tracker.py first.")
        sys.exit(1)

    data = aggregate(picks)
    html = build_html(data, args.from_date, args.to_date)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard generated → {OUT_FILE}")
    print(f"  {len(picks)} day(s) of data · {data['totals']['games']} games settled")
