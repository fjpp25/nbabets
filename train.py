"""
train.py
Pulls historical NBA game data from balldontlie.io and trains three
separate models:
  - nba_model_h2h.pkl     predicts home team win probability
  - nba_model_spread.pkl  predicts home team point margin
  - nba_model_totals.pkl  predicts total combined points (over/under)

Run once (or at the start of each season) to produce all three models.

Usage:
    python train.py
"""

import os
import time
import pickle
import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from sklearn.linear_model    import LogisticRegression, Ridge
from sklearn.preprocessing   import StandardScaler
from sklearn.pipeline        import Pipeline
from sklearn.model_selection import cross_val_score


# ── Constants ─────────────────────────────────────────────────────────────────
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"
SEASONS          = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
MIN_GAMES        = 10
MODEL_DIR        = Path("model")

H2H_FEATURES = [
    "home_season_win_pct",    "away_season_win_pct",
    "home_last_10_win_pct",   "away_last_10_win_pct",
    "home_home_win_pct",      "away_away_win_pct",
    "home_avg_point_diff",    "away_avg_point_diff",
    "home_avg_points_for",    "away_avg_points_for",
    "home_avg_points_against","away_avg_points_against",
    "home_rest_days",         "away_rest_days",
    "rest_advantage",
]

SPREAD_FEATURES = [
    "home_season_win_pct",      "away_season_win_pct",
    "home_avg_point_diff",      "away_avg_point_diff",
    "home_home_avg_point_diff", "away_away_avg_point_diff",
    "home_last_10_point_diff",  "away_last_10_point_diff",
    "home_avg_points_for",      "away_avg_points_for",
    "home_avg_points_against",  "away_avg_points_against",
    "home_rest_days",           "away_rest_days",
    "rest_advantage",
]

TOTALS_FEATURES = [
    "home_avg_total_points",    "away_avg_total_points",
    "home_last_10_avg_total",   "away_last_10_avg_total",
    "home_avg_points_for",      "away_avg_points_for",
    "home_avg_points_against",  "away_avg_points_against",
    "home_over_rate",           "away_over_rate",
    "home_rest_days",           "away_rest_days",
]


# ── Data fetching ─────────────────────────────────────────────────────────────
def fetch_all_games(api_key: str, season: str) -> list[dict]:
    print(f"  Fetching {season} games...", flush=True)
    all_games, cursor = [], None

    while True:
        params = {"seasons[]": int(season[:4]), "per_page": 100}
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
                wait = 2 ** attempt
                print(f"    Rate limited, retrying in {wait}s...", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("Too many retries — balldontlie rate limit")

        data = resp.json()
        all_games.extend([g for g in data["data"] if g["status"] == "Final"])

        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor
        time.sleep(1)

    print(f"    {len(all_games)} games found.")
    return all_games


def fetch_all_seasons(api_key: str) -> list[dict]:
    all_games = []
    for season in SEASONS:
        all_games.extend(fetch_all_games(api_key, season))
    all_games.sort(key=lambda g: g["date"])
    return all_games


# ── Feature engineering ───────────────────────────────────────────────────────
def build_datasets(games: list[dict]) -> dict:
    """
    Builds three separate (X, y) datasets in a single rolling pass.
    Returns {"h2h": (X, y), "spread": (X, y), "totals": (X, y)}
    """
    print("\nBuilding feature matrices (single rolling pass)...")
    team_history: dict[int, list[dict]] = {}

    h2h_X,    h2h_y    = [], []
    spread_X, spread_y = [], []
    totals_X, totals_y = [], []
    skipped = 0

    for g in games:
        home_id = g["home_team"]["id"]
        away_id = g["visitor_team"]["id"]

        home_hist = team_history.get(home_id, [])
        away_hist = team_history.get(away_id, [])

        if len(home_hist) >= MIN_GAMES and len(away_hist) >= MIN_GAMES:
            game_date  = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).date()
            home_stats = _stats_from_history(home_id, home_hist, game_date)
            away_stats = _stats_from_history(away_id, away_hist, game_date)

            actual_margin = g["home_team_score"] - g["visitor_team_score"]
            actual_total  = g["home_team_score"] + g["visitor_team_score"]

            # H2H
            h2h_X.append(_h2h_features(home_stats, away_stats))
            h2h_y.append(1 if actual_margin > 0 else 0)

            # Spread (regression: predict actual margin)
            spread_X.append(_spread_features(home_stats, away_stats))
            spread_y.append(float(actual_margin))

            # Totals (regression: predict actual total)
            totals_X.append(_totals_features(home_stats, away_stats))
            totals_y.append(float(actual_total))
        else:
            skipped += 1

        team_history.setdefault(home_id, []).append(g)
        team_history.setdefault(away_id, []).append(g)

    n = len(h2h_y)
    print(f"  {n} training samples built, {skipped} skipped.")
    return {
        "h2h":    (np.array(h2h_X),    np.array(h2h_y)),
        "spread": (np.array(spread_X), np.array(spread_y)),
        "totals": (np.array(totals_X), np.array(totals_y)),
    }


def _stats_from_history(team_id: int, history: list[dict], as_of_date) -> dict:
    """Computes team stats from game history up to a given date."""
    recent = history[-30:]

    all_results, home_results, away_results = [], [], []
    point_diffs, home_diffs, away_diffs     = [], [], []
    pts_for, pts_against, total_pts         = [], [], []
    home_pts_for, away_pts_for              = [], []

    for g in recent:
        is_home    = g["home_team"]["id"] == team_id
        team_score = g["home_team_score"]    if is_home else g["visitor_team_score"]
        opp_score  = g["visitor_team_score"] if is_home else g["home_team_score"]

        won  = 1 if team_score > opp_score else 0
        diff = team_score - opp_score
        tot  = team_score + opp_score

        all_results.append(won)
        point_diffs.append(diff)
        pts_for.append(team_score)
        pts_against.append(opp_score)
        total_pts.append(tot)

        if is_home:
            home_results.append(won)
            home_diffs.append(diff)
            home_pts_for.append(team_score)
        else:
            away_results.append(won)
            away_diffs.append(diff)
            away_pts_for.append(team_score)

    last_date = datetime.fromisoformat(
        recent[-1]["date"].replace("Z", "+00:00")
    ).date()
    rest_days = min((as_of_date - last_date).days, 7)
    n         = len(all_results)

    def pct(w, t): return w / t if t > 0 else 0.5
    def avg(v):    return sum(v) / len(v) if v else 0.0

    median_total = sorted(total_pts)[n // 2] if total_pts else 220
    over_rate    = sum(1 for t in total_pts if t > median_total) / n if n else 0.5

    return {
        "season_win_pct":       pct(sum(all_results), n),
        "last_10_win_pct":      pct(sum(all_results[-10:]), min(n, 10)),
        "home_win_pct":         pct(sum(home_results), len(home_results)),
        "away_win_pct":         pct(sum(away_results), len(away_results)),
        "avg_point_diff":       avg(point_diffs),
        "home_avg_point_diff":  avg(home_diffs),
        "away_avg_point_diff":  avg(away_diffs),
        "last_10_point_diff":   avg(point_diffs[-10:]),
        "avg_points_for":       avg(pts_for),
        "avg_points_against":   avg(pts_against),
        "avg_total_points":     avg(total_pts),
        "last_10_avg_total":    avg(total_pts[-10:]),
        "avg_points_for_home":  avg(home_pts_for),
        "avg_points_for_away":  avg(away_pts_for),
        "over_rate":            over_rate,
        "rest_days":            float(rest_days),
    }


def _h2h_features(h: dict, a: dict) -> list:
    return [
        h["season_win_pct"],      a["season_win_pct"],
        h["last_10_win_pct"],     a["last_10_win_pct"],
        h["home_win_pct"],        a["away_win_pct"],
        h["avg_point_diff"],      a["avg_point_diff"],
        h["avg_points_for"],      a["avg_points_for"],
        h["avg_points_against"],  a["avg_points_against"],
        h["rest_days"],           a["rest_days"],
        h["rest_days"] - a["rest_days"],
    ]


def _spread_features(h: dict, a: dict) -> list:
    return [
        h["season_win_pct"],         a["season_win_pct"],
        h["avg_point_diff"],         a["avg_point_diff"],
        h["home_avg_point_diff"],    a["away_avg_point_diff"],
        h["last_10_point_diff"],     a["last_10_point_diff"],
        h["avg_points_for"],         a["avg_points_for"],
        h["avg_points_against"],     a["avg_points_against"],
        h["rest_days"],              a["rest_days"],
        h["rest_days"] - a["rest_days"],
    ]


def _totals_features(h: dict, a: dict) -> list:
    return [
        h["avg_total_points"],    a["avg_total_points"],
        h["last_10_avg_total"],   a["last_10_avg_total"],
        h["avg_points_for"],      a["avg_points_for"],
        h["avg_points_against"],  a["avg_points_against"],
        h["over_rate"],           a["over_rate"],
        h["rest_days"],           a["rest_days"],
    ]


# ── Training ──────────────────────────────────────────────────────────────────
def train_all(datasets: dict) -> dict:
    """Trains all three models and returns them as a dict."""
    models = {}

    # H2H — logistic regression (classification)
    print("\n── H2H Model (logistic regression) ──────────────────────")
    X, y = datasets["h2h"]
    pipe = Pipeline([("scaler", StandardScaler()),
                     ("clf", LogisticRegression(max_iter=1000, random_state=42))])
    cv_acc = cross_val_score(pipe, X, y, cv=5, scoring="accuracy")
    cv_ll  = cross_val_score(pipe, X, y, cv=5, scoring="neg_log_loss")
    print(f"  CV Accuracy:  {cv_acc.mean():.3f} ± {cv_acc.std():.3f}")
    print(f"  CV Log Loss:  {-cv_ll.mean():.3f} ± {cv_ll.std():.3f}")
    print(f"  Baseline (always home): {y.mean():.3f}")
    pipe.fit(X, y)
    models["h2h"] = {"pipeline": pipe, "features": H2H_FEATURES, "type": "classification"}
    _print_coefficients(pipe, H2H_FEATURES)

    # Spread — ridge regression (predict actual margin)
    print("\n── Spread Model (ridge regression) ──────────────────────")
    X, y = datasets["spread"]
    pipe = Pipeline([("scaler", StandardScaler()),
                     ("clf", Ridge(alpha=1.0))])
    cv_mae = cross_val_score(pipe, X, y, cv=5, scoring="neg_mean_absolute_error")
    cv_r2  = cross_val_score(pipe, X, y, cv=5, scoring="r2")
    print(f"  CV MAE: {-cv_mae.mean():.2f} pts ± {cv_mae.std():.2f}")
    print(f"  CV R²:  {cv_r2.mean():.3f} ± {cv_r2.std():.3f}")
    pipe.fit(X, y)
    models["spread"] = {"pipeline": pipe, "features": SPREAD_FEATURES, "type": "regression"}
    _print_coefficients(pipe, SPREAD_FEATURES)

    # Totals — ridge regression (predict actual total)
    print("\n── Totals Model (ridge regression) ──────────────────────")
    X, y = datasets["totals"]
    pipe = Pipeline([("scaler", StandardScaler()),
                     ("clf", Ridge(alpha=1.0))])
    cv_mae = cross_val_score(pipe, X, y, cv=5, scoring="neg_mean_absolute_error")
    cv_r2  = cross_val_score(pipe, X, y, cv=5, scoring="r2")
    print(f"  CV MAE: {-cv_mae.mean():.2f} pts ± {cv_mae.std():.2f}")
    print(f"  CV R²:  {cv_r2.mean():.3f} ± {cv_r2.std():.3f}")
    pipe.fit(X, y)
    models["totals"] = {"pipeline": pipe, "features": TOTALS_FEATURES, "type": "regression"}
    _print_coefficients(pipe, TOTALS_FEATURES)

    return models


def _print_coefficients(pipe: Pipeline, features: list) -> None:
    clf  = pipe.named_steps["clf"]
    coef = clf.coef_[0] if hasattr(clf.coef_, "__len__") and clf.coef_.ndim > 1 else clf.coef_
    print("  Feature importances:")
    for name, c in sorted(zip(features, coef), key=lambda x: abs(x[1]), reverse=True)[:8]:
        print(f"    {name:40s} {c:+.4f}")


# ── Persistence ───────────────────────────────────────────────────────────────
def save_models(models: dict, datasets: dict) -> None:
    MODEL_DIR.mkdir(exist_ok=True)
    name_map = {
        "h2h":    "nba_model_h2h.pkl",
        "spread": "nba_model_spread.pkl",
        "totals": "nba_model_totals.pkl",
    }
    for key, filename in name_map.items():
        X, y    = datasets[key]
        payload = {
            **models[key],
            "trained_on":    datetime.now(timezone.utc).isoformat(),
            "n_samples":     len(y),
            "seasons":       SEASONS,
        }
        path = MODEL_DIR / filename
        with open(path, "wb") as f:
            pickle.dump(payload, f)
        print(f"  Saved {path}  ({len(y)} samples)")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_dotenv()
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    print("=" * 57)
    print("  NBA Betting Model — Training Pipeline (3 models)")
    print("=" * 57)

    print("\nStep 1: Fetching historical game data")
    games = fetch_all_seasons(api_key)
    print(f"  Total: {len(games)} games\n")

    print("Step 2: Building feature matrices")
    datasets = build_datasets(games)

    print("\nStep 3: Training models")
    models = train_all(datasets)

    print("\nStep 4: Saving models")
    save_models(models, datasets)

    print("\nDone! Run dry_run.py to start making daily predictions.")
