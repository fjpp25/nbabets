"""
model.py
Loads all three trained models and generates predictions for:
  - H2H      (who wins)
  - Spread   (predicted margin)
  - Totals   (predicted combined score, over/under)

Depends on train.py having been run first.
"""

import pickle
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

from nba_stats import _summarise_games, _bdl_get_all_teams, _find_team, _bdl_get_recent_games


MODEL_DIR = Path("model")


# ── Model loading ─────────────────────────────────────────────────────────────
def load_models() -> dict:
    """Loads all three model payloads from disk."""
    models = {}
    for key, filename in [
        ("h2h",    "nba_model_h2h.pkl"),
        ("spread", "nba_model_spread.pkl"),
        ("totals", "nba_model_totals.pkl"),
    ]:
        path = MODEL_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Model not found: {path}. Run train.py first."
            )
        with open(path, "rb") as f:
            models[key] = pickle.load(f)
    return models


# ── Public interface ──────────────────────────────────────────────────────────
def predict_game(
    home_team:     str,
    away_team:     str,
    bdl_api_key:   str,
    models:        dict,
    total_line:    float = None,
    spread_line:   float = None,
) -> dict:
    """
    Predicts all three markets for a single matchup.

    Returns:
    {
        "home_team":         "Boston Celtics",
        "away_team":         "Miami Heat",

        "h2h": {
            "home_win_prob":    0.68,
            "away_win_prob":    0.32,
            "predicted_winner": "Boston Celtics",
            "confidence":       "high",
        },

        "spread": {
            "predicted_margin":  +6.2,       # positive = home team wins by X
            "predicted_winner":  "Boston Celtics",
            "covers_spread":     True,        # whether home team covers the book's line
            "book_spread":       -5.5,        # book's line for home team (None if unavailable)
        },

        "totals": {
            "predicted_total":   218.4,
            "book_total":        221.5,       # book's line (None if unavailable)
            "prediction":        "Under",     # "Over" or "Under" vs book line
            "margin":            -3.1,        # predicted - book line (negative = lean Under)
        },

        "features": { ... }
    }
    """
    home_stats, away_stats, feature_dict = _build_stats(
        home_team, away_team, bdl_api_key
    )

    return {
        "home_team": home_team,
        "away_team": away_team,
        "h2h":       _predict_h2h(home_stats, away_stats, models["h2h"]),
        "spread":    _predict_spread(home_stats, away_stats, models["spread"], spread_line),
        "totals":    _predict_totals(home_stats, away_stats, models["totals"], total_line),
        "features":  feature_dict,
    }


def predict_all_games(
    games:       list[dict],
    bdl_api_key: str,
    models:      dict,
) -> list[dict]:
    """
    Runs predictions for all of today's games.
    Games should include consensus_spread and consensus_total from odds_fetcher.
    Returns predictions sorted by H2H confidence (most confident first).
    """
    from odds_fetcher import consensus_spread, consensus_total

    predictions = []
    for game in games:
        print(f"  Predicting: {game['away_team']} @ {game['home_team']}...", end=" ")
        try:
            total_line  = consensus_total(game)
            spread_line = consensus_spread(game, game["home_team"])
            pred = predict_game(
                game["home_team"],
                game["away_team"],
                bdl_api_key,
                models,
                total_line=total_line,
                spread_line=spread_line,
            )
            predictions.append(pred)
            h2h     = pred["h2h"]
            winner  = h2h["predicted_winner"]
            prob    = max(h2h["home_win_prob"], h2h["away_win_prob"])
            total_p = pred["totals"]["predicted_total"]
            margin  = pred["spread"]["predicted_margin"]
            print(f"{winner} ({prob:.0%}) | margin: {margin:+.1f} | total: {total_p:.1f}")
        except Exception as e:
            print(f"FAILED — {e}")

    predictions.sort(
        key=lambda p: abs(p["h2h"]["home_win_prob"] - 0.5),
        reverse=True
    )
    return predictions


# ── Market predictors ─────────────────────────────────────────────────────────
def _predict_h2h(home_stats: dict, away_stats: dict, model: dict) -> dict:
    features     = _h2h_features(home_stats, away_stats)
    proba        = model["pipeline"].predict_proba([features])[0]
    home_win_prob = float(proba[1])
    away_win_prob = float(proba[0])
    return {
        "home_win_prob":    round(home_win_prob, 4),
        "away_win_prob":    round(away_win_prob, 4),
        "predicted_winner": "home" if home_win_prob >= 0.5 else "away",   # resolved below
        "confidence":       _confidence_label(home_win_prob),
    }


def _predict_spread(
    home_stats:  dict,
    away_stats:  dict,
    model:       dict,
    spread_line: float = None,
) -> dict:
    features          = _spread_features(home_stats, away_stats)
    predicted_margin  = float(model["pipeline"].predict([features])[0])
    covers_spread     = None
    if spread_line is not None:
        # spread_line is from home team's perspective (negative = home favoured)
        # home covers if predicted margin > -spread_line
        covers_spread = predicted_margin > (-spread_line)

    return {
        "predicted_margin": round(predicted_margin, 1),
        "predicted_winner": "home" if predicted_margin > 0 else "away",
        "covers_spread":    covers_spread,
        "book_spread":      spread_line,
    }


def _predict_totals(
    home_stats: dict,
    away_stats: dict,
    model:      dict,
    total_line: float = None,
) -> dict:
    features        = _totals_features(home_stats, away_stats)
    predicted_total = float(model["pipeline"].predict([features])[0])
    prediction      = None
    margin          = None
    if total_line is not None:
        margin     = round(predicted_total - total_line, 1)
        prediction = "Over" if predicted_total > total_line else "Under"

    return {
        "predicted_total": round(predicted_total, 1),
        "book_total":      total_line,
        "prediction":      prediction,
        "margin":          margin,
    }


# ── Feature builders ──────────────────────────────────────────────────────────
def _build_stats(
    home_team:   str,
    away_team:   str,
    bdl_api_key: str,
) -> tuple[dict, dict, dict]:
    """Fetches recent games and builds stats for both teams."""
    all_teams  = _bdl_get_all_teams(bdl_api_key)
    home_bdl   = _find_team(all_teams, home_team)
    away_bdl   = _find_team(all_teams, away_team)

    home_games = _bdl_get_recent_games(home_bdl["id"], bdl_api_key)
    away_games = _bdl_get_recent_games(away_bdl["id"], bdl_api_key)

    home_stats = _summarise_games(home_bdl["id"], home_games)
    away_stats = _summarise_games(away_bdl["id"], away_games)

    # safe fallbacks for None values
    for stats in (home_stats, away_stats):
        for key, default in [
            ("rest_days", 2), ("home_win_pct", 0.5), ("away_win_pct", 0.5),
            ("home_avg_point_diff", 0.0), ("away_avg_point_diff", 0.0),
            ("last_10_point_diff", 0.0), ("avg_total_points", 220.0),
            ("last_10_avg_total", 220.0), ("over_rate", 0.5),
        ]:
            if stats.get(key) is None:
                stats[key] = default

    feature_dict = {
        "home_" + k: v for k, v in home_stats.items() if k != "recent_form"
    }
    feature_dict.update({
        "away_" + k: v for k, v in away_stats.items() if k != "recent_form"
    })

    return home_stats, away_stats, feature_dict


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


def _confidence_label(home_win_prob: float) -> str:
    margin = abs(home_win_prob - 0.5)
    if margin >= 0.20:   return "high"
    elif margin >= 0.10: return "medium"
    else:                return "low"


# ── Resolve home/away to team names ───────────────────────────────────────────
def resolve_predictions(predictions: list[dict]) -> list[dict]:
    """Replaces 'home'/'away' labels with actual team names."""
    for p in predictions:
        ht, at = p["home_team"], p["away_team"]
        if p["h2h"]["predicted_winner"] == "home":
            p["h2h"]["predicted_winner"] = ht
        else:
            p["h2h"]["predicted_winner"] = at

        if p["spread"]["predicted_winner"] == "home":
            p["spread"]["predicted_winner"] = ht
        else:
            p["spread"]["predicted_winner"] = at
    return predictions


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, json
    from dotenv import load_dotenv
    load_dotenv()

    bdl_key = os.getenv("BALLDONTLIE_API_KEY")
    if not bdl_key:
        raise ValueError("BALLDONTLIE_API_KEY not set in .env file")

    print("Loading models...")
    models = load_models()
    for key, m in models.items():
        print(f"  {key}: trained on {m['n_samples']} samples ({', '.join(m['seasons'])})")

    print("\nPredicting: Miami Heat @ Boston Celtics")
    print("  Book total: 221.5 | Book spread (home): -5.5\n")

    pred = predict_game(
        "Boston Celtics", "Miami Heat", bdl_key, models,
        total_line=221.5, spread_line=-5.5
    )
    pred = resolve_predictions([pred])[0]

    print(f"  H2H:    {pred['h2h']['predicted_winner']} "
          f"({max(pred['h2h']['home_win_prob'], pred['h2h']['away_win_prob']):.0%}) "
          f"— {pred['h2h']['confidence'].upper()}")
    print(f"  Spread: predicted margin {pred['spread']['predicted_margin']:+.1f} "
          f"vs line {pred['spread']['book_spread']:+.1f} "
          f"→ {'COVERS' if pred['spread']['covers_spread'] else 'NO COVER'}")
    print(f"  Totals: predicted {pred['totals']['predicted_total']:.1f} "
          f"vs line {pred['totals']['book_total']} "
          f"→ {pred['totals']['prediction']} "
          f"(margin: {pred['totals']['margin']:+.1f})")
