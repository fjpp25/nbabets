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

from nba_stats   import _summarise_games, _bdl_get_all_teams, _find_team, _bdl_get_recent_games
from injuries    import get_injury_report, get_team_injury_impact, apply_injury_adjustments


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
    injury_report: list  = None,
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
    home_stats, away_stats, feature_dict, home_team_id, away_team_id = _build_stats(
        home_team, away_team, bdl_api_key
    )

    h2h_pred    = _predict_h2h(home_stats, away_stats, models["h2h"])
    spread_pred = _predict_spread(home_stats, away_stats, models["spread"], spread_line)
    totals_pred = _predict_totals(home_stats, away_stats, models["totals"], total_line)

    # back-to-back penalty — second night of b2b reduces win prob and expected scoring
    home_b2b = home_stats.get("is_b2b", False)
    away_b2b = away_stats.get("is_b2b", False)
    B2B_WIN_PENALTY   = 0.03   # 3% win prob reduction for b2b team
    B2B_TOTAL_PENALTY = 3.0    # 3 fewer points expected in a b2b game per team

    if home_b2b or away_b2b:
        home_prob = h2h_pred["home_win_prob"]
        away_prob = h2h_pred["away_win_prob"]
        if home_b2b:
            home_prob -= B2B_WIN_PENALTY
        if away_b2b:
            away_prob -= B2B_WIN_PENALTY
        # renormalise
        total = home_prob + away_prob
        h2h_pred["home_win_prob_b2b_raw"] = h2h_pred["home_win_prob"]
        h2h_pred["away_win_prob_b2b_raw"] = h2h_pred["away_win_prob"]
        h2h_pred["home_win_prob"] = round(home_prob / total, 4)
        h2h_pred["away_win_prob"] = round(away_prob / total, 4)
        h2h_pred["predicted_winner"] = "home" if h2h_pred["home_win_prob"] >= 0.5 else "away"
        h2h_pred["confidence"] = _confidence_label(h2h_pred["home_win_prob"])
        # totals: b2b games tend to be lower scoring
        b2b_total_adj = (B2B_TOTAL_PENALTY if home_b2b else 0) + (B2B_TOTAL_PENALTY if away_b2b else 0)
        if totals_pred.get("predicted_total"):
            totals_pred["predicted_total"] = round(
                totals_pred["predicted_total"] - b2b_total_adj, 1
            )
            if totals_pred.get("book_total"):
                totals_pred["margin"] = round(
                    totals_pred["predicted_total"] - totals_pred["book_total"], 1
                )
                totals_pred["prediction"] = (
                    "Over" if totals_pred["predicted_total"] > totals_pred["book_total"]
                    else "Under"
                )
        h2h_pred["b2b_flag"] = {
            "home_b2b": home_b2b,
            "away_b2b": away_b2b,
        }

    # apply injury adjustments if report available
    injury_impact   = None
    injury_affected = False
    injury_summary  = {}

    if injury_report is not None:
        try:
            # reuse team IDs already fetched in _build_stats — no extra API call
            if home_team_id and away_team_id:
                injury_impact = get_team_injury_impact(
                    home_team_id, away_team_id, injury_report, bdl_api_key
                )
                if (injury_impact["home"]["adjustment"] != 0.0 or
                        injury_impact["away"]["adjustment"] != 0.0):
                    adj_home, adj_away = apply_injury_adjustments(
                        h2h_pred["home_win_prob"],
                        h2h_pred["away_win_prob"],
                        injury_impact,
                    )
                    # preserve original raw probs
                    h2h_pred["home_win_prob_raw"] = h2h_pred["home_win_prob"]
                    h2h_pred["away_win_prob_raw"] = h2h_pred["away_win_prob"]
                    h2h_pred["home_win_prob"]     = adj_home
                    h2h_pred["away_win_prob"]     = adj_away
                    h2h_pred["predicted_winner"]  = "home" if adj_home >= 0.5 else "away"
                    h2h_pred["confidence"]        = _confidence_label(adj_home)

                injury_affected = (
                    injury_impact["home"]["affected"] or
                    injury_impact["away"]["affected"]
                )

                # adjust totals prediction for missing scorers
                home_pts_adj = injury_impact["home"].get("pts_adjustment", 0.0)
                away_pts_adj = injury_impact["away"].get("pts_adjustment", 0.0)
                total_pts_adj = home_pts_adj + away_pts_adj
                if total_pts_adj != 0.0 and totals_pred.get("predicted_total"):
                    raw_total = totals_pred["predicted_total"]
                    adj_total = round(raw_total + total_pts_adj, 1)
                    totals_pred["predicted_total_raw"] = raw_total
                    totals_pred["predicted_total"]     = adj_total
                    totals_pred["injury_pts_adj"]       = round(total_pts_adj, 2)
                    # recalculate over/under vs book line
                    if totals_pred.get("book_total"):
                        margin = round(adj_total - totals_pred["book_total"], 1)
                        totals_pred["margin"]     = margin
                        totals_pred["prediction"] = "Over" if adj_total > totals_pred["book_total"] else "Under"

                injury_summary = {
                    "home_adjustment":     injury_impact["home"]["adjustment"],
                    "away_adjustment":     injury_impact["away"]["adjustment"],
                    "home_pts_adjustment": home_pts_adj,
                    "away_pts_adjustment": away_pts_adj,
                    "total_pts_adjustment":total_pts_adj,
                    "home_injuries":       injury_impact["home"]["injuries"],
                    "away_injuries":       injury_impact["away"]["injuries"],
                }
        except Exception as e:
            pass   # injuries are non-critical, never crash prediction

    return {
        "home_team":        home_team,
        "away_team":        away_team,
        "h2h":              h2h_pred,
        "spread":           spread_pred,
        "totals":           totals_pred,
        "features":         feature_dict,
        "injury_affected":  injury_affected,
        "injury_summary":   injury_summary,
    }


def predict_all_games(
    games:         list[dict],
    bdl_api_key:   str,
    models:        dict,
    injury_report: list = None,
) -> list[dict]:
    """
    Runs predictions for all of today's games.
    Pre-warms all balldontlie caches BEFORE fetching injuries to avoid
    rate limit collisions between game fetches and PPG lookups.
    """
    from odds_fetcher import pinnacle_spread, pinnacle_total

    # ── Phase 1: pre-warm all team/game caches ────────────────────────────────
    # Fetch teams list + all team game histories FIRST so they are cached.
    # This way the injury PPG lookups (Phase 2) don't compete with game fetches.
    print("  Pre-warming stats cache...", end=" ", flush=True)
    try:
        all_teams = _bdl_get_all_teams(bdl_api_key)
        team_ids  = set()
        for game in games:
            home_bdl = _find_team(all_teams, game["home_team"])
            away_bdl = _find_team(all_teams, game["away_team"])
            if home_bdl: team_ids.add(home_bdl["id"])
            if away_bdl: team_ids.add(away_bdl["id"])
        for tid in team_ids:
            _bdl_get_recent_games(tid, bdl_api_key)
        print(f"{len(team_ids)} teams cached")
    except Exception as e:
        print(f"warning ({e})")

    # ── Phase 2: fetch injury report (uses PPG lookups) ───────────────────────
    if injury_report is None:
        print("  Fetching injury report...", end=" ", flush=True)
        try:
            injury_report = get_injury_report(bdl_api_key)
            print(f"{len(injury_report)} players listed")
        except Exception as e:
            print(f"failed ({e}) — proceeding without injury data")
            injury_report = []
    else:
        print(f"  Using pre-fetched injury report ({len(injury_report)} players)")

    # ── Phase 3: run predictions (all from cache, no new API calls) ───────────
    predictions = []
    for game in games:
        print(f"  Predicting: {game['away_team']} @ {game['home_team']}...", end=" ")
        try:
            total_line  = pinnacle_total(game)
            spread_line = pinnacle_spread(game, game["home_team"])
            pred = predict_game(
                game["home_team"],
                game["away_team"],
                bdl_api_key,
                models,
                total_line=total_line,
                spread_line=spread_line,
                injury_report=injury_report,
            )
            predictions.append(pred)
            h2h     = pred["h2h"]
            winner  = h2h["predicted_winner"]
            prob    = max(h2h["home_win_prob"], h2h["away_win_prob"])
            total_p = pred["totals"]["predicted_total"]
            margin  = pred["spread"]["predicted_margin"]
            b2b = pred["h2h"].get("b2b_flag", {})
            b2b_str  = ""
            if b2b.get("home_b2b"): b2b_str = f" B2B({pred['home_team'].split()[-1]})"
            if b2b.get("away_b2b"): b2b_str = f" B2B({pred['away_team'].split()[-1]})"
            inj_flag = (" ⚠" if pred.get("injury_affected") else "") + b2b_str
            print(f"{winner} ({prob:.0%}) | margin: {margin:+.1f} | total: {total_p:.1f}{inj_flag}")
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

    return home_stats, away_stats, feature_dict, home_bdl["id"], away_bdl["id"]


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
