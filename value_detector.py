"""
value_detector.py
Compares model predictions against bookmaker odds across three markets:
  - H2H      (moneyline)
  - Spread   (point handicap)
  - Totals   (over/under)

For H2H: edge = model probability - bookmaker implied probability
For Spread/Totals: edge = difference between predicted line and book line,
    only flagged when best available odds are also favourable (> 1.80)
"""

from odds_fetcher import (
    best_h2h_odds, best_spread_odds, best_total_odds, implied_probability,
    pinnacle_h2h_odds, pinnacle_spread, pinnacle_total
)


# ── Constants ─────────────────────────────────────────────────────────────────
MIN_EDGE          = 0.03    # minimum probability edge for H2H bets (3%)
MIN_CONFIDENCE    = 0.55    # model must be at least 55% confident for H2H
MIN_SPREAD_MARGIN = 2.0     # model must disagree with book line by 2+ pts
MIN_TOTAL_MARGIN  = 5.0     # model must disagree with total line by 5+ pts
MIN_ODDS          = 1.75    # only flag spread/total bets with odds >= this
SIMULATED_STAKE   = 10.0    # € base stake per bet in the dry run
BANKROLL          = 1000.0  # simulated bankroll for Kelly sizing
KELLY_FRACTION    = 0.25    # fractional Kelly (0.25 = quarter Kelly — conservative)
MAX_BET           = 50.0    # cap per bet regardless of Kelly

PREFERRED_BOOKS = [
    "betclic_fr",
    "unibet_fr",
    "unibet_nl",
    "pinnacle",
    "williamhill",
]

# ── Risk scoring ──────────────────────────────────────────────────────────────
RISK_WEIGHTS = {
    "confidence": 0.25,
    "volatility": 0.25,
    "sample":     0.20,
    "rest":       0.15,
    "odds":       0.15,
}

RISK_LABELS = [(0, 30, "LOW"), (31, 60, "MEDIUM"), (61, 100, "HIGH")]


def compute_risk_score(
    model_prob:  float,
    odds:        float,
    rest_days:   int   = 2,
    volatility:  float = None,
    sample_size: int   = 10,
) -> dict:
    """Returns {"score": int, "label": str, "components": dict}"""
    confidence_risk = (1 - abs(model_prob - 0.5) * 2) * 100
    volatility_risk = min(volatility / 15.0 * 100, 100) if volatility is not None else 50
    sample_risk     = max(0, min(100, (10 - sample_size) / 6 * 100))
    rest_risk       = max(0, min(100, (2 - min(rest_days, 2)) / 2 * 100))
    odds_risk       = min(100, max(0, (odds - 1.5) / 2.0 * 100))

    components = {
        "confidence": round(confidence_risk, 1),
        "volatility": round(volatility_risk, 1),
        "sample":     round(sample_risk, 1),
        "rest":       round(rest_risk, 1),
        "odds":       round(odds_risk, 1),
    }
    score = round(min(100, max(0, sum(components[k] * RISK_WEIGHTS[k] for k in components))))
    label = next(lbl for lo, hi, lbl in RISK_LABELS if lo <= score <= hi)
    return {"score": score, "label": label, "components": components}


def kelly_stake(model_prob: float, decimal_odds: float) -> float:
    """
    Computes a fractional Kelly stake.

    Kelly formula: f = (bp - q) / b
      where b = decimal_odds - 1 (net odds)
            p = model probability of winning
            q = 1 - p

    We use quarter-Kelly for safety and cap at MAX_BET.
    Returns the recommended stake in euros.
    """
    b = decimal_odds - 1
    p = model_prob
    q = 1 - p
    if b <= 0 or p <= 0:
        return SIMULATED_STAKE
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return SIMULATED_STAKE   # no edge, use base stake
    stake = BANKROLL * KELLY_FRACTION * full_kelly
    return round(min(max(stake, SIMULATED_STAKE), MAX_BET), 2)


# ── Public interface ──────────────────────────────────────────────────────────
def find_value_bets(predictions: list[dict], games: list[dict]) -> list[dict]:
    """
    Scans all predictions and games for value across all three markets.
    Returns all value bets sorted by market then edge.
    """
    odds_lookup = {g["id"]: g for g in games}
    value_bets  = []

    for pred in predictions:
        odds_game = _match_game(pred, odds_lookup)
        if not odds_game:
            continue

        # H2H
        value_bets.extend(_evaluate_h2h(pred, odds_game))

        # Spread
        spread_bet = _evaluate_spread(pred, odds_game)
        if spread_bet:
            value_bets.append(spread_bet)

        # Totals
        total_bet = _evaluate_total(pred, odds_game)
        if total_bet:
            value_bets.append(total_bet)

    value_bets.sort(key=lambda b: (b.get("risk_score", 50), -abs(b.get("edge", 0))))
    return value_bets


def summarise_value_bets(value_bets: list[dict]) -> None:
    """Prints value bets sorted by risk score (lowest risk first)."""
    if not value_bets:
        print("  No value bets found today.")
        return

    market_labels = {"h2h": "MONEYLINE", "spread": "SPREAD", "totals": "TOTALS"}
    for i, bet in enumerate(value_bets, 1):
        market_key   = bet["market"]
        market_label = market_labels.get(market_key, market_key.upper())
        risk_score   = bet.get("risk_score", "—")
        risk_label   = bet.get("risk_label", "—")

        print(f"\n  #{i}  [{market_label}]  {bet['game']}")
        print(f"       Bet:      {bet['bet_label']}")
        print(f"       Odds:     {bet['best_odds']} ({bet['bookmaker']})")

        if market_key == "h2h":
            ref = f" [{bet.get('ref_source','consensus')}]"
            print(f"       Model:    {bet['model_prob']*100:.1f}%  |  "
                  f"Implied: {bet['implied_prob']*100:.1f}%{ref}  |  "
                  f"Edge: {bet['edge']*100:+.1f}%")
        elif market_key == "spread":
            print(f"       Model predicts: {bet['predicted']:+.1f} pts margin (home team)")
            print(f"       Book line:      {bet['book_line']:+.1f} (home team)  →  "
                  f"betting {bet['bet_label']}  |  Disagreement: {abs(bet['edge']):.1f} pts")
        elif market_key == "totals":
            direction = "above" if bet["edge"] > 0 else "below"
            print(f"       Model predicts: {bet['predicted']:.1f} combined pts  "
                  f"({abs(bet['edge']):.1f} pts {direction} the line)")
            print(f"       Book line:      {bet['book_line']:.1f}  →  betting {bet['bet_label']}")

        print(f"       Simulated: €{bet['simulated_stake']:.2f} stake → "
              f"€{bet['simulated_return']:.2f} return "
              f"(€{bet['simulated_profit']:+.2f} if correct)")
        print(f"       Risk:      {risk_score}/100 ({risk_label})  |  "
              f"Confidence: {bet['confidence'].upper()}")

    total_staked = sum(b["simulated_stake"]  for b in value_bets)
    total_return = sum(b["simulated_return"] for b in value_bets)
    total_profit = sum(b["simulated_profit"] for b in value_bets)
    print(f"\n  {'─'*60}")
    print(f"  Total simulated stake:  €{total_staked:.2f}")
    print(f"  Total potential return: €{total_return:.2f}")
    print(f"  Total potential profit: €{total_profit:+.2f}")
    print(f"  {'='*60}\n")


def find_contrarian_picks(predictions: list[dict], games: list[dict]) -> list[dict]:
    """
    Flags games where our model disagrees with the bookmaker's favourite.
    """
    odds_lookup = {g["id"]: g for g in games}
    contrarians = []

    for pred in predictions:
        odds_game = _match_game(pred, odds_lookup)
        if not odds_game:
            continue

        home_odds, _ = best_h2h_odds(odds_game, pred["home_team"])
        away_odds, _ = best_h2h_odds(odds_game, pred["away_team"])
        if not home_odds or not away_odds:
            continue

        book_favourite  = pred["home_team"] if home_odds < away_odds else pred["away_team"]
        model_favourite = pred["h2h"]["predicted_winner"]

        if book_favourite != model_favourite:
            fair_home, fair_away = _remove_vig(home_odds, away_odds)
            book_prob  = fair_home  if book_favourite == pred["home_team"] else fair_away
            model_prob = (pred["h2h"]["home_win_prob"]
                          if model_favourite == pred["home_team"]
                          else pred["h2h"]["away_win_prob"])

            contrarians.append({
                "game":           f"{pred['away_team']} @ {pred['home_team']}",
                "book_favourite": book_favourite,
                "book_prob":      round(book_prob, 4),
                "model_pick":     model_favourite,
                "model_prob":     round(model_prob, 4),
                "confidence":     pred["h2h"]["confidence"],
            })

    return contrarians


def summarise_contrarian_picks(contrarians: list[dict]) -> None:
    if not contrarians:
        print("  No contrarian picks today.")
        return

    print(f"\n  {'─'*60}")
    print(f"  CONTRARIAN PICKS — model disagrees with {len(contrarians)} bookmaker favourite(s)")
    print(f"  {'─'*60}")
    for c in contrarians:
        print(f"\n  {c['game']}")
        print(f"    Book favourite: {c['book_favourite']:25s} ({c['book_prob']*100:.1f}% implied)")
        print(f"    Model pick:     {c['model_pick']:25s} ({c['model_prob']*100:.1f}% model prob)")
        print(f"    Confidence: {c['confidence'].upper()}")
    print()


# ── H2H evaluation ────────────────────────────────────────────────────────────
def _evaluate_h2h(pred: dict, odds_game: dict) -> list[dict]:
    bets = []
    for team, model_prob in [
        (pred["home_team"], pred["h2h"]["home_win_prob"]),
        (pred["away_team"], pred["h2h"]["away_win_prob"]),
    ]:
        if model_prob < MIN_CONFIDENCE:
            continue

        best_odds, book = best_h2h_odds(odds_game, team)
        if not best_odds:
            continue

        # Use Pinnacle as reference (sharpest market), fall back to consensus
        pin_home, pin_away = pinnacle_h2h_odds(odds_game, team)
        if pin_home and pin_away:
            fair_home, fair_away = _remove_vig(pin_home, pin_away)
            ref_source = "Pinnacle"
        else:
            home_odds, _ = best_h2h_odds(odds_game, pred["home_team"])
            away_odds, _ = best_h2h_odds(odds_game, pred["away_team"])
            if not home_odds or not away_odds:
                continue
            fair_home, fair_away = _remove_vig(home_odds, away_odds)
            ref_source = "consensus"
        implied_prob = fair_home if team == pred["home_team"] else fair_away
        edge = model_prob - implied_prob

        if edge < MIN_EDGE:
            continue

        home_rest = pred["features"].get("home_rest_days", 2)
        away_rest = pred["features"].get("away_rest_days", 2)
        team_rest = home_rest if team == pred["home_team"] else away_rest
        risk = compute_risk_score(
            model_prob=model_prob,
            odds=best_odds,
            rest_days=int(team_rest),
        )
        stake = kelly_stake(model_prob, best_odds)
        bets.append({
            "market":           "h2h",
            "game":             f"{pred['away_team']} @ {pred['home_team']}",
            "commence_time":    odds_game["commence_time"],
            "bet_label":        f"{team} to win",
            "bet_team":         team,
            "model_prob":       round(model_prob, 4),
            "implied_prob":     round(implied_prob, 4),
            "ref_source":       ref_source,
            "edge":             round(edge, 4),
            "best_odds":        best_odds,
            "bookmaker":        book,
            "simulated_stake":  stake,
            "simulated_return": round(stake * best_odds, 2),
            "simulated_profit": round(stake * best_odds - stake, 2),
            "confidence":       pred["h2h"]["confidence"],
            "risk_score":       risk["score"],
            "risk_label":       risk["label"],
            "risk_components":  risk["components"],
            "outcome":          None,
            "actual_pnl":       None,
        })
    return bets


# ── Spread evaluation ─────────────────────────────────────────────────────────
def _evaluate_spread(pred: dict, odds_game: dict) -> dict | None:
    spread_pred = pred.get("spread", {})
    predicted_margin = spread_pred.get("predicted_margin")
    book_spread      = spread_pred.get("book_spread")

    if predicted_margin is None or book_spread is None:
        return None

    # book_spread is for home team (negative = home favoured)
    # model says home wins by predicted_margin
    # edge = how much the model's implied spread differs from the book's
    edge = predicted_margin - (-book_spread)   # positive = model thinks home covers by more

    if abs(edge) < MIN_SPREAD_MARGIN:
        return None

    # bet the team the model thinks covers
    if edge > 0:
        bet_team = pred["home_team"]
        bet_label = f"{pred['home_team']} {book_spread:+.1f}"
        best_odds, best_line, book = best_spread_odds(odds_game, pred["home_team"])
    else:
        bet_team = pred["away_team"]
        away_spread = -book_spread
        bet_label = f"{pred['away_team']} {away_spread:+.1f}"
        best_odds, best_line, book = best_spread_odds(odds_game, pred["away_team"])

    MAX_SPREAD_ODDS = 2.60
    if not best_odds or best_odds < MIN_ODDS or best_odds > MAX_SPREAD_ODDS:
        return None

    home_rest = pred["features"].get("home_rest_days", 2)
    away_rest = pred["features"].get("away_rest_days", 2)
    bet_rest  = home_rest if bet_team == pred["home_team"] else away_rest
    model_prob = max(pred["h2h"]["home_win_prob"], pred["h2h"]["away_win_prob"])
    risk = compute_risk_score(
        model_prob=model_prob,
        odds=best_odds,
        rest_days=int(bet_rest),
    )
    stake = kelly_stake(model_prob, best_odds)
    return {
        "market":           "spread",
        "game":             f"{pred['away_team']} @ {pred['home_team']}",
        "commence_time":    odds_game["commence_time"],
        "bet_label":        bet_label,
        "bet_team":         bet_team,
        "predicted":        predicted_margin,
        "book_line":        book_spread,
        "edge":             round(edge, 1),
        "best_odds":        best_odds,
        "bookmaker":        book,
        "simulated_stake":  stake,
        "simulated_return": round(stake * best_odds, 2),
        "simulated_profit": round(stake * best_odds - stake, 2),
        "confidence":       pred["h2h"]["confidence"],
        "risk_score":       risk["score"],
        "risk_label":       risk["label"],
        "risk_components":  risk["components"],
        "outcome":          None,
        "actual_pnl":       None,
    }


# ── Totals evaluation ─────────────────────────────────────────────────────────
def _evaluate_total(pred: dict, odds_game: dict) -> dict | None:
    totals_pred     = pred.get("totals", {})
    predicted_total = totals_pred.get("predicted_total")
    book_total      = totals_pred.get("book_total")
    margin          = totals_pred.get("margin")
    side            = totals_pred.get("prediction")   # "Over" or "Under"

    if predicted_total is None or book_total is None or margin is None:
        return None

    if abs(margin) < MIN_TOTAL_MARGIN:
        return None

    best_odds, best_line, book = best_total_odds(odds_game, side)
    if not best_odds or best_odds < MIN_ODDS:
        return None

    home_rest  = pred["features"].get("home_rest_days", 2)
    away_rest  = pred["features"].get("away_rest_days", 2)
    avg_rest   = (home_rest + away_rest) / 2
    model_prob = max(pred["h2h"]["home_win_prob"], pred["h2h"]["away_win_prob"])
    risk = compute_risk_score(
        model_prob=model_prob,
        odds=best_odds,
        rest_days=int(avg_rest),
    )
    stake = kelly_stake(model_prob, best_odds)
    return {
        "market":           "totals",
        "game":             f"{pred['away_team']} @ {pred['home_team']}",
        "commence_time":    odds_game["commence_time"],
        "bet_label":        f"{side} {book_total}",
        "bet_side":         side,
        "predicted":        predicted_total,
        "book_line":        book_total,
        "edge":             round(margin, 1),
        "best_odds":        best_odds,
        "bookmaker":        book,
        "simulated_stake":  stake,
        "simulated_return": round(stake * best_odds, 2),
        "simulated_profit": round(stake * best_odds - stake, 2),
        "confidence":       pred["h2h"]["confidence"],
        "risk_score":       risk["score"],
        "risk_label":       risk["label"],
        "risk_components":  risk["components"],
        "outcome":          None,
        "actual_pnl":       None,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
def _match_game(pred: dict, odds_lookup: dict) -> dict | None:
    for game in odds_lookup.values():
        if (_names_match(game["home_team"], pred["home_team"]) and
                _names_match(game["away_team"], pred["away_team"])):
            return game
    return None


def _remove_vig(home_odds: float, away_odds: float) -> tuple[float, float]:
    home_implied = 1 / home_odds
    away_implied = 1 / away_odds
    total        = home_implied + away_implied
    return home_implied / total, away_implied / total


def _names_match(a: str, b: str) -> bool:
    a_words = {w.lower() for w in a.split() if len(w) > 3}
    b_words = {w.lower() for w in b.split() if len(w) > 3}
    return bool(a_words & b_words)
