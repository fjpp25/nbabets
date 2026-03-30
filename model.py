"""
model.py
Loads the trained logistic regression model and predicts win probabilities
for today's matchups.

Depends on: train.py having been run first (produces model/nba_model.pkl)
"""

import pickle
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

from nba_stats import _summarise_games, _bdl_get_all_teams, _find_team, _bdl_get_recent_games


MODEL_PATH = Path("model/nba_model.pkl")


# ── Public interface ──────────────────────────────────────────────────────────
def load_model() -> dict:
    """Loads the trained model payload from disk."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            "Model not found. Run train.py first to generate model/nba_model.pkl"
        )
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


def predict_game(home_team: str, away_team: str, bdl_api_key: str, model_payload: dict) -> dict:
    """
    Predicts win probabilities for a single matchup.

    Returns:
    {
        "home_team":       "Boston Celtics",
        "away_team":       "Miami Heat",
        "home_win_prob":   0.68,
        "away_win_prob":   0.32,
        "predicted_winner": "Boston Celtics",
        "confidence":      "medium",     # low / medium / high
        "features":        { ... }       # feature values used
    }
    """
    pipeline = model_payload["pipeline"]
    features, feature_dict = _build_features(home_team, away_team, bdl_api_key)

    proba         = pipeline.predict_proba([features])[0]
    home_win_prob = float(proba[1])
    away_win_prob = float(proba[0])

    return {
        "home_team":        home_team,
        "away_team":        away_team,
        "home_win_prob":    round(home_win_prob, 4),
        "away_win_prob":    round(away_win_prob, 4),
        "predicted_winner": home_team if home_win_prob >= 0.5 else away_team,
        "confidence":       _confidence_label(home_win_prob),
        "features":         feature_dict,
    }


def predict_all_games(games: list[dict], bdl_api_key: str, model_payload: dict) -> list[dict]:
    """
    Runs predictions for a list of games (as returned by odds_fetcher).
    Returns predictions sorted by confidence (most confident first).
    """
    predictions = []
    for game in games:
        print(f"  Predicting: {game['away_team']} @ {game['home_team']}...", end=" ")
        try:
            pred = predict_game(
                game["home_team"],
                game["away_team"],
                bdl_api_key,
                model_payload,
            )
            predictions.append(pred)
            print(f"{pred['predicted_winner']} ({max(pred['home_win_prob'], pred['away_win_prob']):.0%})")
        except Exception as e:
            print(f"FAILED — {e}")

    # sort by margin of confidence (furthest from 50/50)
    predictions.sort(key=lambda p: abs(p["home_win_prob"] - 0.5), reverse=True)
    return predictions


# ── Feature builder ───────────────────────────────────────────────────────────
def _build_features(home_team: str, away_team: str, bdl_api_key: str) -> tuple[list, dict]:
    """
    Fetches recent games for both teams and computes the feature vector,
    matching the exact same features used during training.
    """
    all_teams  = _bdl_get_all_teams(bdl_api_key)
    home_bdl   = _find_team(all_teams, home_team)
    away_bdl   = _find_team(all_teams, away_team)

    home_games = _bdl_get_recent_games(home_bdl["id"], bdl_api_key)
    away_games = _bdl_get_recent_games(away_bdl["id"], bdl_api_key)

    home_stats = _summarise_games(home_bdl["id"], home_games)
    away_stats = _summarise_games(away_bdl["id"], away_games)

    # safe fallback for any None values (e.g. rest_days if no recent games found)
    for stats in (home_stats, away_stats):
        for key, default in [("rest_days", 2), ("home_win_pct", 0.5), ("away_win_pct", 0.5)]:
            if stats.get(key) is None:
                stats[key] = default

    feature_vector = [
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
        home_stats["rest_days"] - away_stats["rest_days"],
    ]

    feature_dict = {
        "home_season_win_pct":     home_stats["season_win_pct"],
        "away_season_win_pct":     away_stats["season_win_pct"],
        "home_last_10_win_pct":    home_stats["last_10_win_pct"],
        "away_last_10_win_pct":    away_stats["last_10_win_pct"],
        "home_home_win_pct":       home_stats["home_win_pct"],
        "away_away_win_pct":       away_stats["away_win_pct"],
        "home_avg_point_diff":     home_stats["avg_point_diff"],
        "away_avg_point_diff":     away_stats["avg_point_diff"],
        "home_avg_points_for":     home_stats["avg_points_for"],
        "away_avg_points_for":     away_stats["avg_points_for"],
        "home_avg_points_against": home_stats["avg_points_against"],
        "away_avg_points_against": away_stats["avg_points_against"],
        "home_rest_days":          home_stats["rest_days"],
        "away_rest_days":          away_stats["rest_days"],
        "rest_advantage":          home_stats["rest_days"] - away_stats["rest_days"],
    }

    return feature_vector, feature_dict


def _confidence_label(home_win_prob: float) -> str:
    margin = abs(home_win_prob - 0.5)
    if margin >= 0.20:
        return "high"
    elif margin >= 0.10:
        return "medium"
    else:
        return "low"


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, json
    from dotenv import load_dotenv
    load_dotenv()

    bdl_key = os.getenv("BALLDONTLIE_API_KEY")
    if not bdl_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    print("Loading model...")
    payload = load_model()
    print(f"Model trained on {payload['n_samples']} samples ({', '.join(payload['seasons'])})")
    print(f"Trained at: {payload['trained_on']}\n")

    print("Predicting: Miami Heat @ Boston Celtics\n")
    result = predict_game("Boston Celtics", "Miami Heat", bdl_key, payload)
    print(json.dumps(result, indent=2))
