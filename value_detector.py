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
    best_h2h_odds, best_spread_odds, best_total_odds, implied_probability
)


# ── Constants ─────────────────────────────────────────────────────────────────
MIN_EDGE          = 0.03    # minimum probability edge for H2H bets (3%)
MIN_CONFIDENCE    = 0.55    # model must be at least 55% confident for H2H
MIN_SPREAD_MARGIN = 2.0     # model must disagree with book line by 2+ pts
MIN_TOTAL_MARGIN  = 5.0     # model must disagree with total line by 5+ pts
MIN_ODDS          = 1.75    # only flag spread/total bets with odds >= this
SIMULATED_STAKE   = 10.0    # € per bet in the dry run

PREFERRED_BOOKS = [
    "betclic_fr",
    "unibet_fr",
    "unibet_nl",
    "pinnacle",
    "williamhill",
]


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

    value_bets.sort(key=lambda b: (b["market"], -b.get("edge", 0)))
    return value_bets


def summarise_value_bets(value_bets: list[dict]) -> None:
    """Prints a formatted summary of all value bets grouped by market."""
    if not value_bets:
        print("  No value bets found today.")
        return

    markets = {"h2h": "MONEYLINE", "spread": "SPREAD", "totals": "TOTALS"}
    for market_key, market_label in markets.items():
        bets = [b for b in value_bets if b["market"] == market_key]
        if not bets:
            continue

        print(f"\n  ── {market_label} ({len(bets)} bet(s)) {'─'*40}")
        for i, bet in enumerate(bets, 1):
            print(f"\n  #{i}  {bet['game']}")
            print(f"       Bet:      {bet['bet_label']}")
            print(f"       Odds:     {bet['best_odds']} ({bet['bookmaker']})")

            if market_key == "h2h":
                print(f"       Model:    {bet['model_prob']*100:.1f}%  |  "
                      f"Implied: {bet['implied_prob']*100:.1f}%  |  "
                      f"Edge: {bet['edge']*100:+.1f}%")
            elif market_key == "spread":
                # always show from the perspective of the bet team
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
            print(f"       Confidence: {bet['confidence'].upper()}")

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

        home_odds, _ = best_h2h_odds(odds_game, pred["home_team"])
        away_odds, _ = best_h2h_odds(odds_game, pred["away_team"])
        if not home_odds or not away_odds:
            continue

        fair_home, fair_away = _remove_vig(home_odds, away_odds)
        implied_prob = fair_home if team == pred["home_team"] else fair_away
        edge = model_prob - implied_prob

        if edge < MIN_EDGE:
            continue

        bets.append({
            "market":           "h2h",
            "game":             f"{pred['away_team']} @ {pred['home_team']}",
            "commence_time":    odds_game["commence_time"],
            "bet_label":        f"{team} to win",
            "bet_team":         team,
            "model_prob":       round(model_prob, 4),
            "implied_prob":     round(implied_prob, 4),
            "edge":             round(edge, 4),
            "best_odds":        best_odds,
            "bookmaker":        book,
            "simulated_stake":  SIMULATED_STAKE,
            "simulated_return": round(SIMULATED_STAKE * best_odds, 2),
            "simulated_profit": round(SIMULATED_STAKE * best_odds - SIMULATED_STAKE, 2),
            "confidence":       pred["h2h"]["confidence"],
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

    if not best_odds or best_odds < MIN_ODDS:
        return None

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
        "simulated_stake":  SIMULATED_STAKE,
        "simulated_return": round(SIMULATED_STAKE * best_odds, 2),
        "simulated_profit": round(SIMULATED_STAKE * best_odds - SIMULATED_STAKE, 2),
        "confidence":       pred["h2h"]["confidence"],
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
        "simulated_stake":  SIMULATED_STAKE,
        "simulated_return": round(SIMULATED_STAKE * best_odds, 2),
        "simulated_profit": round(SIMULATED_STAKE * best_odds - SIMULATED_STAKE, 2),
        "confidence":       pred["h2h"]["confidence"],
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
