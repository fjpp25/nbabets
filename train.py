"""
train.py
Pulls historical NBA game data from balldontlie.io, builds a feature matrix,
and trains a logistic regression model to predict home team win probability.

Run once (or at the start of each season) to produce: model/nba_model.pkl

Usage:
    python train.py
"""

import os
import json
import time
import pickle
import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import cross_val_score
from sklearn.metrics import log_loss


# ── Constants ─────────────────────────────────────────────────────────────────
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"
SEASONS          = ["2023-24", "2024-25"]   # seasons to train on
MIN_GAMES        = 10                        # min prior games needed to build features
MODEL_DIR        = Path("model")
MODEL_PATH       = MODEL_DIR / "nba_model.pkl"
FEATURE_NAMES    = [
    "home_season_win_pct",
    "away_season_win_pct",
    "home_last_10_win_pct",
    "away_last_10_win_pct",
    "home_home_win_pct",
    "away_away_win_pct",
    "home_avg_point_diff",
    "away_avg_point_diff",
    "home_avg_points_for",
    "away_avg_points_for",
    "home_avg_points_against",
    "away_avg_points_against",
    "home_rest_days",
    "away_rest_days",
    "rest_advantage",          # home_rest - away_rest
]


# ── Data fetching ─────────────────────────────────────────────────────────────
def fetch_all_games(api_key: str, season: str) -> list[dict]:
    """Fetches all completed games for a given season from balldontlie."""
    print(f"  Fetching {season} games...", end=" ", flush=True)
    all_games = []
    cursor    = None

    while True:
        params = {
            "seasons[]": int(season[:4]),   # balldontlie uses start year: 2023 for 2023-24
            "per_page":  100,
        }
        if cursor:
            params["cursor"] = cursor

        for attempt in range(5):
            resp = requests.get(
                f"{BALLDONTLIE_BASE}/games",
                headers={"Authorization": api_key},
                params=params,
                timeout=15,
            )
            if resp.status_code == 429:
                wait = 2 ** attempt   # exponential backoff: 1s, 2s, 4s, 8s, 16s
                print(f"Rate limited, retrying in {wait}s...", end=" ", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("Too many retries — balldontlie rate limit")
        data = resp.json()
        time.sleep(1)     # paid tier = 60 req/min → 1s delay is safe

        games = [g for g in data["data"] if g["status"] == "Final"]
        all_games.extend(games)

        # pagination
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor

    print(f"{len(all_games)} games found.")
    return all_games


def fetch_all_seasons(api_key: str) -> list[dict]:
    """Fetches and combines games across all configured seasons."""
    all_games = []
    for season in SEASONS:
        all_games.extend(fetch_all_games(api_key, season))
    # sort chronologically
    all_games.sort(key=lambda g: g["date"])
    return all_games


# ── Feature engineering ───────────────────────────────────────────────────────
def build_dataset(games: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """
    Iterates through games chronologically.
    For each game, computes features from the team's history *before* that game.
    Returns (X, y) arrays.

    This rolling approach prevents data leakage — each game's features only
    use information that would have been available before tip-off.
    """
    print("\nBuilding feature matrix (rolling window — this may take a moment)...")

    # rolling game history per team: team_id -> list of game dicts (chronological)
    team_history: dict[int, list[dict]] = {}

    X_rows, y_rows = [], []
    skipped = 0

    for g in games:
        home_id = g["home_team"]["id"]
        away_id = g["visitor_team"]["id"]

        home_hist = team_history.get(home_id, [])
        away_hist = team_history.get(away_id, [])

        # only use games where both teams have enough history
        if len(home_hist) >= MIN_GAMES and len(away_hist) >= MIN_GAMES:
            game_date = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).date()
            features  = _compute_features(home_id, away_id, home_hist, away_hist, game_date)
            label     = 1 if g["home_team_score"] > g["visitor_team_score"] else 0
            X_rows.append(features)
            y_rows.append(label)
        else:
            skipped += 1

        # update history after the game (important: append AFTER extracting features)
        team_history.setdefault(home_id, []).append(g)
        team_history.setdefault(away_id, []).append(g)

    print(f"  {len(X_rows)} training samples built, {skipped} skipped (insufficient history).")
    return np.array(X_rows), np.array(y_rows)


def _compute_features(
    home_id: int,
    away_id: int,
    home_hist: list[dict],
    away_hist: list[dict],
    game_date,
) -> list[float]:
    """Computes the feature vector for a single matchup from prior game history."""
    home_stats = _stats_from_history(home_id, home_hist, game_date)
    away_stats = _stats_from_history(away_id, away_hist, game_date)

    return [
        home_stats["season_win_pct"],
        away_stats["season_win_pct"],
        home_stats["last_10_win_pct"],
        away_stats["last_10_win_pct"],
        home_stats["home_win_pct"],
        away_stats["away_win_pct"],
        home_stats["avg_point_diff"],
        away_stats["avg_point_diff"],
        home_stats["avg_points_for"],
        away_stats["avg_points_for"],
        home_stats["avg_points_against"],
        away_stats["avg_points_against"],
        home_stats["rest_days"],
        away_stats["rest_days"],
        home_stats["rest_days"] - away_stats["rest_days"],  # rest advantage
    ]


def _stats_from_history(team_id: int, history: list[dict], as_of_date) -> dict:
    """Derives team stats from their game history up to (not including) a given date."""
    # use last 30 games max for recency
    recent = history[-30:]

    all_results, home_results, away_results = [], [], []
    point_diffs, pts_for, pts_against       = [], [], []

    for g in recent:
        is_home    = g["home_team"]["id"] == team_id
        team_score = g["home_team_score"]    if is_home else g["visitor_team_score"]
        opp_score  = g["visitor_team_score"] if is_home else g["home_team_score"]

        won = 1 if team_score > opp_score else 0
        all_results.append(won)
        point_diffs.append(team_score - opp_score)
        pts_for.append(team_score)
        pts_against.append(opp_score)

        if is_home:
            home_results.append(won)
        else:
            away_results.append(won)

    last_date = datetime.fromisoformat(
        recent[-1]["date"].replace("Z", "+00:00")
    ).date()
    rest_days = (as_of_date - last_date).days
    n         = len(all_results)

    def pct(w, t): return w / t if t > 0 else 0.5   # default to 0.5 if no data

    return {
        "season_win_pct":     pct(sum(all_results), n),
        "last_10_win_pct":    pct(sum(all_results[-10:]), min(n, 10)),
        "home_win_pct":       pct(sum(home_results), len(home_results)),
        "away_win_pct":       pct(sum(away_results), len(away_results)),
        "avg_point_diff":     sum(point_diffs) / n,
        "avg_points_for":     sum(pts_for) / n,
        "avg_points_against": sum(pts_against) / n,
        "rest_days":          float(min(rest_days, 7)),  # cap at 7 to avoid outliers
    }


# ── Training ──────────────────────────────────────────────────────────────────
def train_model(X: np.ndarray, y: np.ndarray) -> Pipeline:
    """
    Trains a logistic regression pipeline (scaler + classifier).
    Prints cross-validated accuracy and log loss.
    """
    print("\nTraining logistic regression...")

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    LogisticRegression(max_iter=1000, random_state=42)),
    ])

    # 5-fold cross-validation
    cv_acc = cross_val_score(pipeline, X, y, cv=5, scoring="accuracy")
    cv_ll  = cross_val_score(pipeline, X, y, cv=5, scoring="neg_log_loss")

    print(f"  CV Accuracy:  {cv_acc.mean():.3f} ± {cv_acc.std():.3f}")
    print(f"  CV Log Loss:  {-cv_ll.mean():.3f} ± {cv_ll.std():.3f}")
    print(f"  (Baseline accuracy for always predicting home win: "
          f"{y.mean():.3f})")

    # fit on full dataset
    pipeline.fit(X, y)

    # feature importance (coefficients after scaling)
    coefs = pipeline.named_steps["clf"].coef_[0]
    print("\n  Feature importances (logistic regression coefficients):")
    for name, coef in sorted(zip(FEATURE_NAMES, coefs), key=lambda x: abs(x[1]), reverse=True):
        print(f"    {name:35s} {coef:+.4f}")

    return pipeline


# ── Persistence ───────────────────────────────────────────────────────────────
def save_model(pipeline: Pipeline, X: np.ndarray, y: np.ndarray) -> None:
    """Saves the trained pipeline and metadata to disk."""
    MODEL_DIR.mkdir(exist_ok=True)

    payload = {
        "pipeline":      pipeline,
        "feature_names": FEATURE_NAMES,
        "trained_on":    datetime.now(timezone.utc).isoformat(),
        "n_samples":     len(y),
        "home_win_rate": float(y.mean()),
        "seasons":       SEASONS,
    }
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(payload, f)

    print(f"\nModel saved to {MODEL_PATH}")
    print(f"  Trained on {len(y)} samples | Home win rate: {y.mean():.3f}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_dotenv()
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    print("=" * 55)
    print("  NBA Betting Model — Training Pipeline")
    print("=" * 55)
    print(f"\nSeasons: {', '.join(SEASONS)}")
    print(f"Min prior games required: {MIN_GAMES}\n")

    print("Step 1: Fetching historical game data")
    games = fetch_all_seasons(api_key)
    print(f"  Total games loaded: {len(games)}")

    print("\nStep 2: Building feature matrix")
    X, y = build_dataset(games)

    print("\nStep 3: Training model")
    pipeline = train_model(X, y)

    print("\nStep 4: Saving model")
    save_model(pipeline, X, y)

    print("\nDone! Run dry_run.py to start making daily predictions.")
