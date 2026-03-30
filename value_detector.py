"""
value_detector.py
Compares model-predicted probabilities against bookmaker implied probabilities
to identify value bets — situations where our model thinks a team is more
likely to win than the odds suggest.

Key concept:
    Bookmaker implied probability = 1 / decimal_odds
    If our model says 65% but the bookmaker implies 55%, that's a +10% edge.
    Over many bets, positive edges lead to positive returns (in theory!).

Note on the overround:
    Bookmakers set odds so that implied probabilities sum to >100% (e.g. 105%).
    This "overround" or "vig" is their built-in margin. We remove it before
    comparing, so we're always working with fair probabilities.
"""


# ── Constants ─────────────────────────────────────────────────────────────────
MIN_EDGE          = 0.03   # minimum edge to flag a bet (3%)
MIN_CONFIDENCE    = 0.55   # model must be at least 55% confident to consider betting
SIMULATED_STAKE   = 10.0   # € per bet in the dry run
PREFERRED_BOOKS   = [      # prioritise these bookmakers if available (edit to taste)
    "betclic_fr",
    "unibet_fr",
    "unibet_nl",
    "pinnacle",
    "williamhill",
]


# ── Public interface ──────────────────────────────────────────────────────────
def find_value_bets(predictions: list[dict], games: list[dict]) -> list[dict]:
    """
    Merges model predictions with bookmaker odds and returns a list of
    value bets, sorted by edge (best edge first).

    Each value bet looks like:
    {
        "game":             "Miami Heat @ Boston Celtics",
        "commence_time":    "2025-01-15T00:10:00Z",
        "bet_team":         "Boston Celtics",
        "model_prob":       0.68,
        "implied_prob":     0.55,        # bookmaker's fair probability (vig removed)
        "edge":             0.13,        # model_prob - implied_prob
        "best_odds":        1.82,        # best decimal odds available
        "bookmaker":        "Unibet",
        "simulated_stake":  10.0,
        "simulated_return": 18.20,       # stake × odds if bet wins
        "simulated_profit": 8.20,        # return - stake
        "confidence":       "high",
    }
    """
    # build a lookup from game id → odds game
    odds_lookup = {g["id"]: g for g in games}

    value_bets = []
    for pred in predictions:
        # find the matching odds game
        odds_game = _match_game(pred, odds_lookup)
        if not odds_game:
            continue

        # check both teams for value
        for team, model_prob in [
            (pred["home_team"], pred["home_win_prob"]),
            (pred["away_team"], pred["away_win_prob"]),
        ]:
            if model_prob < MIN_CONFIDENCE:
                continue

            bet = _evaluate_bet(team, model_prob, odds_game, pred)
            if bet:
                value_bets.append(bet)

    # sort by edge descending
    value_bets.sort(key=lambda b: b["edge"], reverse=True)
    return value_bets


def summarise_value_bets(value_bets: list[dict]) -> None:
    """Prints a formatted summary of today's value bets to the console."""
    if not value_bets:
        print("No value bets found today.")
        return

    print(f"\n{'='*65}")
    print(f"  VALUE BETS TODAY — {len(value_bets)} found")
    print(f"{'='*65}")

    for i, bet in enumerate(value_bets, 1):
        edge_pct     = f"{bet['edge']*100:+.1f}%"
        model_pct    = f"{bet['model_prob']*100:.1f}%"
        implied_pct  = f"{bet['implied_prob']*100:.1f}%"
        print(f"\n  #{i}  {bet['game']}")
        print(f"       Bet:       {bet['bet_team']}")
        print(f"       Odds:      {bet['best_odds']} ({bet['bookmaker']})")
        print(f"       Model:     {model_pct}  |  Implied: {implied_pct}  |  Edge: {edge_pct}")
        print(f"       Simulated: €{bet['simulated_stake']:.2f} stake → "
              f"€{bet['simulated_return']:.2f} return  "
              f"(€{bet['simulated_profit']:+.2f} profit if correct)")
        print(f"       Confidence: {bet['confidence'].upper()}")

    total_staked   = sum(b["simulated_stake"]   for b in value_bets)
    total_return   = sum(b["simulated_return"]  for b in value_bets)
    total_profit   = sum(b["simulated_profit"]  for b in value_bets)
    print(f"\n{'─'*65}")
    print(f"  Total simulated stake:  €{total_staked:.2f}")
    print(f"  Total potential return: €{total_return:.2f}")
    print(f"  Total potential profit: €{total_profit:+.2f}")
    print(f"{'='*65}\n")


# ── Core bet evaluation ───────────────────────────────────────────────────────
def _evaluate_bet(
    team: str,
    model_prob: float,
    odds_game: dict,
    pred: dict,
) -> dict | None:
    """
    Checks whether a team represents a value bet.
    Returns a bet dict if edge >= MIN_EDGE, otherwise None.
    """
    # get best odds across all bookmakers for this team
    best_odds, best_book = _get_best_odds(team, odds_game)
    if not best_odds:
        return None

    # remove the vig to get the fair implied probability
    home_team  = odds_game["home_team"]
    away_team  = odds_game["away_team"]
    home_odds  = _get_best_odds(home_team, odds_game)[0]
    away_odds  = _get_best_odds(away_team, odds_game)[0]

    if not home_odds or not away_odds:
        return None

    fair_home_prob, fair_away_prob = _remove_vig(home_odds, away_odds)
    implied_prob = fair_home_prob if team == home_team else fair_away_prob

    edge = model_prob - implied_prob
    if edge < MIN_EDGE:
        return None

    return {
        "game":             f"{odds_game['away_team']} @ {odds_game['home_team']}",
        "commence_time":    odds_game["commence_time"],
        "bet_team":         team,
        "model_prob":       round(model_prob, 4),
        "implied_prob":     round(implied_prob, 4),
        "edge":             round(edge, 4),
        "best_odds":        best_odds,
        "bookmaker":        best_book,
        "simulated_stake":  SIMULATED_STAKE,
        "simulated_return": round(SIMULATED_STAKE * best_odds, 2),
        "simulated_profit": round(SIMULATED_STAKE * best_odds - SIMULATED_STAKE, 2),
        "confidence":       pred["confidence"],
    }


def _get_best_odds(team: str, odds_game: dict) -> tuple[float | None, str]:
    """
    Returns (best_decimal_odds, bookmaker_name) for a team across all
    available bookmakers, prioritising preferred books on ties.
    """
    best_odds = None
    best_book = ""

    # check preferred bookmakers first
    for bm in odds_game["bookmakers"]:
        h2h = bm["markets"].get("h2h", {})
        # fuzzy team name match
        matched_odds = next(
            (odds for t, odds in h2h.items() if _names_match(t, team)),
            None
        )
        if matched_odds is None:
            continue

        is_preferred = bm["key"] in PREFERRED_BOOKS
        if (
            best_odds is None
            or matched_odds > best_odds
            or (matched_odds == best_odds and is_preferred)
        ):
            best_odds = matched_odds
            best_book = bm["title"]

    return best_odds, best_book


def _remove_vig(home_odds: float, away_odds: float) -> tuple[float, float]:
    """
    Converts raw bookmaker odds to fair (vig-free) probabilities.

    The overround is the excess implied probability above 100%.
    We normalise both sides so they sum to exactly 1.0.

    Example:
        Home odds 1.80 → implied 55.6%
        Away odds 2.10 → implied 47.6%
        Total implied = 103.2%  (3.2% is the vig)
        Fair home prob = 55.6% / 103.2% = 53.9%
        Fair away prob = 47.6% / 103.2% = 46.1%
    """
    home_implied = 1 / home_odds
    away_implied = 1 / away_odds
    total        = home_implied + away_implied

    return home_implied / total, away_implied / total


def _match_game(pred: dict, odds_lookup: dict) -> dict | None:
    """Finds the odds game matching a prediction by team name."""
    for game in odds_lookup.values():
        if (
            _names_match(game["home_team"], pred["home_team"])
            and _names_match(game["away_team"], pred["away_team"])
        ):
            return game
    return None


def _names_match(a: str, b: str) -> bool:
    """Fuzzy team name match — checks if any meaningful word overlaps."""
    a_words = {w.lower() for w in a.split() if len(w) > 3}
    b_words = {w.lower() for w in b.split() if len(w) > 3}
    return bool(a_words & b_words)



def find_contrarian_picks(predictions: list[dict], games: list[dict]) -> list[dict]:
    """
    Flags games where our model disagrees with the bookmaker's favourite.
    i.e. the book prices team A as favourite, but our model prefers team B.

    These aren't necessarily value bets, but they're worth tracking during
    the dry run to see how often the model's contrarian view is correct.
    """
    odds_lookup  = {g["id"]: g for g in games}
    contrarians  = []

    for pred in predictions:
        odds_game = _match_game(pred, odds_lookup)
        if not odds_game:
            continue

        home_odds, _ = _get_best_odds(pred["home_team"], odds_game)
        away_odds, _ = _get_best_odds(pred["away_team"], odds_game)
        if not home_odds or not away_odds:
            continue

        # who does the book favour?
        book_favourite = pred["home_team"] if home_odds < away_odds else pred["away_team"]

        # who does our model favour?
        model_favourite = pred["predicted_winner"]

        if book_favourite != model_favourite:
            fair_home, fair_away = _remove_vig(home_odds, away_odds)
            book_prob  = fair_home  if book_favourite == pred["home_team"] else fair_away
            model_prob = pred["home_win_prob"] if model_favourite == pred["home_team"] else pred["away_win_prob"]

            contrarians.append({
                "game":           f"{pred['away_team']} @ {pred['home_team']}",
                "book_favourite": book_favourite,
                "book_prob":      round(book_prob, 4),
                "model_pick":     model_favourite,
                "model_prob":     round(model_prob, 4),
                "confidence":     pred["confidence"],
            })

    return contrarians


def summarise_contrarian_picks(contrarians: list[dict]) -> None:
    """Prints a formatted summary of contrarian picks."""
    if not contrarians:
        print("  No contrarian picks today (model agrees with all bookmaker favourites).")
        return

    print(f"\n{'-'*65}")
    print(f"  CONTRARIAN PICKS — {len(contrarians)} game(s) where model disagrees with the book")
    print(f"{'-'*65}")
    for c in contrarians:
        print(f"\n  {c['game']}")
        print(f"    Book favourite: {c['book_favourite']:25s} ({c['book_prob']*100:.1f}% implied)")
        print(f"    Model pick:     {c['model_pick']:25s} ({c['model_prob']*100:.1f}% model prob)")
        print(f"    Confidence: {c['confidence'].upper()}")
    print()

# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Simulate a prediction and a fake odds game to verify the logic
    mock_predictions = [
        {
            "home_team":        "Boston Celtics",
            "away_team":        "Miami Heat",
            "home_win_prob":    0.68,
            "away_win_prob":    0.32,
            "predicted_winner": "Boston Celtics",
            "confidence":       "high",
        }
    ]

    mock_games = [
        {
            "id":            "test-001",
            "home_team":     "Boston Celtics",
            "away_team":     "Miami Heat",
            "commence_time": "2025-03-30T00:10:00Z",
            "bookmakers": [
                {
                    "key":   "unibet_eu",
                    "title": "Unibet",
                    "markets": {
                        "h2h": {
                            "Boston Celtics": 1.72,
                            "Miami Heat":     2.15,
                        }
                    }
                }
            ]
        }
    ]

    bets = find_value_bets(mock_predictions, mock_games)
    summarise_value_bets(bets)
