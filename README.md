# NBA Betting Model

A daily NBA betting analysis pipeline that fetches odds, predicts outcomes using machine learning, and identifies value bets across four markets: moneyline (H2H), spread, totals (over/under), and player props.

Built as a dry-run tool — simulates bets without risking real money, tracking predicted vs actual outcomes over time to validate model performance.

---

## How it works

```
odds_fetcher.py     → today's games + odds (The Odds API)
nba_stats.py        → team performance history (balldontlie.io)
model.py            → win/spread/total predictions (trained ML models)
value_detector.py   → H2H, spread, and totals value bets
player_props.py     → player prop value bets
dry_run.py          → daily entry point, saves picks to data/
results_tracker.py  → settles yesterday's picks with actual outcomes
report.py           → cumulative P&L and accuracy report
morning_run.bat     → one-click daily script (Windows)
train.py            → trains all three models from historical data
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <your-repo-url>
cd nba_betting
pip install -r requirements.txt
```

### 2. API keys

You need two API keys:

| Service | Purpose | URL |
|---|---|---|
| The Odds API | Game odds across all markets | [the-odds-api.com](https://the-odds-api.com) |
| balldontlie.io | NBA game results and player stats | [balldontlie.io](https://www.balldontlie.io) |

Copy `.env.template` to `.env` and fill in your keys:

```bash
cp .env.template .env
```

```
ODDS_API_KEY=your_key_here
BALLDONTLIE_API_KEY=your_key_here
```

> **Never commit `.env` to git.** It's already in `.gitignore`.

### 3. Train the models

Run once before the first dry run (and again at the start of each season):

```bash
python train.py
```

This fetches 5 seasons of historical game data (~6,000+ games) from balldontlie and trains three models:

- `model/nba_model_h2h.pkl` — logistic regression, predicts win probability
- `model/nba_model_spread.pkl` — ridge regression, predicts point margin
- `model/nba_model_totals.pkl` — ridge regression, predicts combined score

Training takes ~5 minutes on the paid balldontlie tier (60 req/min).

---

## Daily usage

### Option A — One click (Windows)

Double-click `morning_run.bat` every morning. It runs all three steps in order.

### Option B — Manual

```bash
# Every morning after games finish:
python results_tracker.py       # settle yesterday's picks
python report.py                # view cumulative P&L

# Before tonight's games:
python dry_run.py               # generate today's picks
```

To settle a specific date:

```bash
python results_tracker.py 2026-03-30
```

---

## Markets covered

### H2H (moneyline)
Predicts win probability for each team. Flags bets where model probability exceeds the bookmaker's implied probability by **≥3%** (configurable in `value_detector.py`).

### Spread (point handicap)
Predicts the home team's point margin. Flags bets where the model's predicted margin disagrees with the book line by **≥2 points**.

### Totals (over/under)
Predicts the combined score. Flags over or under bets where the model's prediction differs from the book line by **≥5 points**.

### Player props
Compares each player's rolling average (last 10 games) against the bookmaker's prop line. Flags bets where the average differs from the line by **≥2 units** and odds are **≥1.75**.

Markets: points, rebounds, assists.

---

## Model performance (5 seasons: 2021-22 to 2025-26)

| Model | Type | CV Accuracy | Baseline |
|---|---|---|---|
| H2H | Logistic regression | 64.5% ± 1.4% | 55.4% |
| Spread | Ridge regression | MAE 11.1 pts | — |
| Totals | Ridge regression | MAE 15.0 pts | — |

---

## Output

Each day's picks are saved to `data/picks_YYYY-MM-DD.json` and updated the next morning with actual outcomes. The JSON contains:

- All game predictions (H2H, spread, totals)
- Value bets with simulated stake and P&L
- Contrarian picks (where model disagrees with book favourite)
- Player prop bets
- Results summary after settlement

---

## Configuration

Key thresholds are defined at the top of each module:

| File | Setting | Default | Description |
|---|---|---|---|
| `value_detector.py` | `MIN_EDGE` | 0.03 | Minimum H2H edge to flag a bet |
| `value_detector.py` | `MIN_SPREAD_MARGIN` | 2.0 pts | Minimum spread disagreement |
| `value_detector.py` | `MIN_TOTAL_MARGIN` | 5.0 pts | Minimum total disagreement |
| `value_detector.py` | `SIMULATED_STAKE` | €10.00 | Stake per bet in dry run |
| `player_props.py` | `MIN_EDGE_PTS` | 2.0 | Minimum prop edge in units |
| `player_props.py` | `LOOKBACK_GAMES` | 10 | Games used for rolling average |
| `player_props.py` | `MIN_ODDS` | 1.75 | Minimum odds to flag a prop |
| `train.py` | `SEASONS` | 5 seasons | Seasons used for training |
| `nba_stats.py` | `LOOKBACK_DAYS` | 90 | Days of recent games to fetch |

---

## Project structure

```
nba_betting/
│
├── train.py                  # trains all three ML models
├── dry_run.py                # daily entry point
├── results_tracker.py        # settles picks after games finish
├── report.py                 # cumulative report
├── morning_run.bat           # one-click Windows script
│
├── odds_fetcher.py           # The Odds API client
├── nba_stats.py              # balldontlie team stats client
├── model.py                  # loads models, generates predictions
├── value_detector.py         # H2H / spread / totals value detection
├── player_props.py           # player prop fetching and evaluation
│
├── model/                    # trained model files (not in git)
│   ├── nba_model_h2h.pkl
│   ├── nba_model_spread.pkl
│   └── nba_model_totals.pkl
│
├── data/                     # daily picks JSON files (not in git)
│   └── picks_YYYY-MM-DD.json
│
├── .env                      # API keys (not in git)
├── .env.template             # template for .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Disclaimer

This project is for educational and research purposes only. It is not financial advice. Sports betting involves risk — never bet more than you can afford to lose. Always check local laws regarding sports betting before wagering real money.
