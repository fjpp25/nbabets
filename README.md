# NBA Betting Model

A daily NBA betting analysis pipeline that fetches live odds, predicts outcomes using machine learning, adjusts for injuries and back-to-backs, and identifies value bets across four markets: moneyline, spread, totals, and player props.

Built as a **dry-run tool** — simulates bets without risking real money, tracking predicted vs actual outcomes to validate model performance before going live.

---

## How it works

```
┌─────────────────────────────────────────────────────────────────┐
│  Morning run (every day)                                        │
│                                                                 │
│  1. results_tracker.py  →  settle yesterday's picks            │
│  2. report.py           →  cumulative P&L report               │
│  3. dashboard.py        →  regenerate HTML dashboard           │
│  4. dry_run.py          →  today's picks                       │
└─────────────────────────────────────────────────────────────────┘

dry_run.py pipeline (Step 4 in detail):

  Phase 1 — Odds + Stats
    odds_fetcher.py    →  today's games, H2H/spread/totals/props odds
    nba_stats.py       →  team recent game history (balldontlie.io)

  Phase 2 — Injury report
    injuries.py        →  player injuries, PPG lookups, impact scoring

  Phase 3 — Predictions
    model.py           →  win probability, spread, totals (3 ML models)
                          + injury adjustments
                          + back-to-back adjustments

  Phase 4 — Value detection
    value_detector.py  →  H2H / spread / totals value bets (Kelly sized)
    player_props.py    →  player prop value bets (injured players filtered)

  Output → data/picks_YYYY-MM-DD.json
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. API keys

Two API keys are required:

| Service | Plan needed | Purpose |
|---|---|---|
| The Odds API | Paid (~500 req/month) | Live odds for all markets |
| balldontlie.io | ALL-STAR ($9.99/mo) | Team stats, injuries, standings |

Copy `.env.template` to `.env` and fill in your keys:

```
ODDS_API_KEY=your_key_here
BALLDONTLIE_API_KEY=your_key_here
```

> **Never commit `.env` to git.** It is already in `.gitignore`.

### 3. Train the models

Run once before the first dry run, and again at the start of each new season:

```bash
python train.py
```

Fetches 5 seasons of historical NBA data (~6,000+ games) from balldontlie and trains three models saved to `model/`:

| Model | Type | CV Accuracy |
|---|---|---|
| H2H (nba_model_h2h.pkl) | Logistic regression | 64.5% +/- 1.4% |
| Spread (nba_model_spread.pkl) | Ridge regression | MAE 11.1 pts |
| Totals (nba_model_totals.pkl) | Ridge regression | MAE 15.0 pts |

Training takes approximately 5 minutes on the paid balldontlie tier.

---

## Daily usage

### Option A — Desktop app (recommended)

```bash
pip install PyQt6
python app.py
```

A native Windows application with buttons to fetch today's picks, settle yesterday's results, and open the HTML dashboard.

### Option B — One click (Windows)

Double-click `morning_run.bat`. Runs all four steps in order.

### Option C — Manual

```bash
python results_tracker.py   # settle yesterday's picks
python report.py            # view cumulative P&L
python dashboard.py         # regenerate HTML dashboard
python dry_run.py           # today's picks
```

To settle a specific date:

```bash
python results_tracker.py 2026-03-30
```

---

## Markets covered

### H2H (moneyline)
Predicts win probability using logistic regression. Edge is calculated against Pinnacle's vig-removed implied probability. Bets flagged when model edge >= 3%.

### Spread (point handicap)
Predicts the home team's point margin using ridge regression. Uses Pinnacle's spread line as the reference. Flags bets where the model disagrees with the book line by >= 2 points at odds >= 1.75.

### Totals (over/under)
Predicts the combined score. Uses Pinnacle's total line as reference. Flags bets where model disagrees by >= 5 points. Totals are adjusted for missing scorers and back-to-back fatigue.

### Player props
Compares each player's rolling average (last 10 games) against the bookmaker's line. Players listed as Out or Doubtful in the NBA injury report are automatically skipped. Flags bets where the rolling average differs from the line by >= 2 units at odds >= 1.75.

---

## Adjustments applied to predictions

### Injury adjustments

The full NBA injury report is fetched on each run. For each injured player on tonight's teams, their season scoring average is looked up and they are classified by tier:

| Tier | PPG threshold | Out (win prob) | Out (total pts) |
|---|---|---|---|
| STAR | >= 20 ppg | -10% | -4.0 pts |
| KEY | >= 12 ppg | -5% | -2.0 pts |
| ROLE | < 12 ppg | -1% | -0.5 pts |

Doubtful players receive 60% of the Out adjustment. Questionable players receive 30%. Players averaging fewer than 5 ppg are ignored entirely.

### Back-to-back adjustments
Teams playing the second night of a back-to-back receive -3% win probability and -3 pts from the predicted total.

### Reference line (Pinnacle)
All edge calculations use Pinnacle as the market reference. Falls back to consensus if Pinnacle is unavailable.

---

## Staking — Kelly criterion

Stakes are sized using quarter-Kelly criterion:

```
Full Kelly  = (edge * odds - (1 - edge)) / (odds - 1)
Quarter Kelly = Full Kelly * 0.25 * bankroll
Final stake = min(max(Kelly, 10), 50)   # capped between €10 and €50
```

Simulated bankroll: EUR 1,000. Adjust BANKROLL in value_detector.py before going live.

---

## Risk scoring

Every value bet is assigned a risk score (0-100):

| Component | Weight | Description |
|---|---|---|
| Confidence | 25% | Distance of model probability from 50% |
| Volatility | 25% | Standard deviation of recent game stats |
| Sample size | 20% | Number of games in the rolling average |
| Rest days | 15% | Days since team last played |
| Odds | 15% | Higher odds = higher variance |

Labels: LOW (0-30), MEDIUM (31-60), HIGH (61-100).

---

## Configuration

| File | Setting | Default | Description |
|---|---|---|---|
| value_detector.py | MIN_EDGE | 0.03 | Minimum H2H edge |
| value_detector.py | MIN_SPREAD_MARGIN | 2.0 pts | Minimum spread disagreement |
| value_detector.py | MIN_TOTAL_MARGIN | 5.0 pts | Minimum total disagreement |
| value_detector.py | BANKROLL | 1000 | Simulated bankroll |
| value_detector.py | KELLY_FRACTION | 0.25 | Fraction of Kelly to use |
| value_detector.py | MAX_BET | 50 | Maximum stake per bet |
| player_props.py | MIN_EDGE_PTS | 2.0 | Minimum prop edge in units |
| player_props.py | LOOKBACK_GAMES | 10 | Games in rolling average |
| injuries.py | STAR_PPG_THRESHOLD | 20.0 | PPG threshold for star tier |
| injuries.py | KEY_PPG_THRESHOLD | 12.0 | PPG threshold for key tier |
| injuries.py | MIN_PPG_THRESHOLD | 5.0 | Minimum PPG to consider |
| injuries.py | MIN_GAMES_FOR_AVG | 10 | Minimum games to trust average |
| nba_stats.py | LOOKBACK_DAYS | 90 | Days of recent history |

---

## Project structure

```
nba_betting/
|
+-- app.py                    # PyQt6 desktop application
+-- dry_run.py                # daily entry point
+-- results_tracker.py        # settles picks after games finish
+-- report.py                 # cumulative terminal report
+-- dashboard.py              # generates dashboard.html
+-- morning_run.bat           # one-click Windows script
|
+-- train.py                  # trains all three ML models
+-- odds_fetcher.py           # The Odds API client
+-- nba_stats.py              # balldontlie team stats
+-- injuries.py               # injury report and impact scoring
+-- model.py                  # predictions + adjustments
+-- value_detector.py         # value detection + Kelly sizing
+-- player_props.py           # prop fetching and evaluation
|
+-- model/                    # trained model files (not in git)
|   +-- nba_model_h2h.pkl
|   +-- nba_model_spread.pkl
|   +-- nba_model_totals.pkl
|
+-- data/                     # picks JSON files (not in git)
|   +-- picks_YYYY-MM-DD.json
|   +-- ppg_cache.json
|
+-- .env                      # API keys (not in git)
+-- .env.template
+-- .gitignore
+-- requirements.txt
+-- README.md
```

---

## Disclaimer

This project is for educational and research purposes only. It is not financial advice. Sports betting involves risk — never bet more than you can afford to lose. Always verify local laws on sports betting before wagering real money.
