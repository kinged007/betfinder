"""
Market Type Definitions and Series Mappings for Kalshi.

Kalshi markets are binary (yes/no) prediction markets grouped into:
  - Series  (e.g. KXNBAGAME)  →  a recurring set of events
  - Events  (e.g. KXNBAGAME-26MAR05UTAWAS)  →  one game
  - Markets (e.g. KXNBAGAME-26MAR05UTAWAS-WAS)  →  one binary outcome

Key data points from the real API:
  - event.title: "Utah at Washington"   ← "AWAY at HOME" for American sports
  - market.yes_sub_title: "Washington"  ← team this market resolves YES for
  - market.yes_ask: 54                  ← implied probability in cents (0–100)
  - Decimal odds = 100 / yes_ask

This module provides:
  1. Predefined series-ticker → internal-league-key mappings
  2. Series-ticker → internal-market-key detection (GAME→h2h, SPREAD→spreads, etc.)
  3. Team extraction from Kalshi event titles
  4. Selection normalisation (team name → 'home'/'away'/'draw')
  5. Title-pattern-based market-key inference (for KalshiMarketType.from_kalshi_title)
"""

from typing import Optional, Dict, List, Tuple
import re

from app.services.bookmakers.base import token_sort_ratio


# ──────────────────────────────────────────────────────────────────────────────
# Predefined series ticker → internal league key
# Keys are Kalshi series tickers; values are The-Odds-API league slugs.
# Only game-level (match winner / spread / total) series are mapped here.
# Outright/championship series are deliberately omitted.
# ──────────────────────────────────────────────────────────────────────────────
SERIES_TO_LEAGUE: Dict[str, str] = {
    # Basketball
    "KXNBAGAME":     "basketball_nba",
    "KXNBASPREAD":   "basketball_nba",
    "KXNBATOTAL":    "basketball_nba",
    "KXNCAAMBGAME":  "basketball_ncaab",
    "KXNCAAWGAME":   "basketball_ncaaw",
    # American Football
    "KXNFLGAME":     "americanfootball_nfl",
    "KXNFLSPREAD":   "americanfootball_nfl",
    "KXNFLTOTAL":    "americanfootball_nfl",
    "KXNCAAFBGAME":  "americanfootball_ncaaf",
    # Baseball
    "KXMLBGAME":     "baseball_mlb",
    "KXMLBSPREAD":   "baseball_mlb",
    "KXMLBTOTAL":    "baseball_mlb",
    # Ice Hockey
    "KXNHLGAME":     "icehockey_nhl",
    "KXNHLSPREAD":   "icehockey_nhl",
    "KXNHLTOTAL":    "icehockey_nhl",
    # Soccer (game-level series, if/when Kalshi adds them)
    "KXEPLGAME":     "soccer_epl",
    "KXUCLGAME":     "soccer_uefa_champs_league",
}

# Reverse: internal league key → primary series ticker (game winner)
LEAGUE_TO_PRIMARY_SERIES: Dict[str, str] = {
    "basketball_nba":          "KXNBAGAME",
    "basketball_ncaab":        "KXNCAAMBGAME",
    "basketball_ncaaw":        "KXNCAAWGAME",
    "americanfootball_nfl":    "KXNFLGAME",
    "americanfootball_ncaaf":  "KXNCAAFBGAME",
    "baseball_mlb":            "KXMLBGAME",
    "icehockey_nhl":           "KXNHLGAME",
    "soccer_epl":              "KXEPLGAME",
    "soccer_uefa_champs_league": "KXUCLGAME",
}

# product_metadata.competition → internal league key (used as fallback when
# a series ticker is not in SERIES_TO_LEAGUE)
COMPETITION_TO_LEAGUE: Dict[str, str] = {
    "Pro Basketball (M)":       "basketball_nba",
    "College Basketball (M)":   "basketball_ncaab",
    "College Basketball (W)":   "basketball_ncaaw",
    "Pro Football (M)":         "americanfootball_nfl",
    "College Football (M)":     "americanfootball_ncaaf",
    "Pro Baseball (M)":         "baseball_mlb",
    "Pro Ice Hockey (M)":       "icehockey_nhl",
}


class KalshiMarketType:
    """
    Utilities for mapping Kalshi market / event structures to internal keys.
    """

    # ── Series-ticker helpers ─────────────────────────────────────────────────

    @classmethod
    def series_to_league(cls, series_ticker: str) -> Optional[str]:
        """Map a Kalshi series ticker to an internal league key."""
        return SERIES_TO_LEAGUE.get(series_ticker)

    @classmethod
    def competition_to_league(cls, competition: str) -> Optional[str]:
        """
        Map a Kalshi product_metadata.competition string to an internal league
        key. Used as fallback when series ticker is not in SERIES_TO_LEAGUE.
        """
        return COMPETITION_TO_LEAGUE.get(competition)

    @classmethod
    def get_series_for_league(cls, league_key: str) -> Optional[str]:
        """
        Return the primary game-winner series ticker for a league key, or None.
        E.g. "basketball_nba" → "KXNBAGAME"
        """
        return LEAGUE_TO_PRIMARY_SERIES.get(league_key)

    @classmethod
    def series_to_market_key(cls, series_ticker: str) -> str:
        """
        Infer internal market key from series ticker suffix convention:
          - *GAME   → h2h  (game winner)
          - *SPREAD → spreads
          - *TOTAL  → totals
        Falls back to 'h2h' for unknown patterns.
        """
        t = series_ticker.upper()
        if t.endswith("SPREAD"):
            return "spreads"
        if t.endswith("TOTAL"):
            return "totals"
        if t.endswith("GAME"):
            return "h2h"
        return "h2h"

    # ── Event title parsing ───────────────────────────────────────────────────

    @classmethod
    def extract_teams_from_event_title(cls, title: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract (home_team, away_team) from a Kalshi event title.

        American sports use "AWAY at HOME":
          "Utah at Washington"  →  away="Utah",  home="Washington"
          "South Florida at Memphis"  →  away="South Florida", home="Memphis"

        Soccer / international use "HOME vs AWAY":
          "Arsenal vs Chelsea"  →  home="Arsenal", away="Chelsea"

        Returns (home_team, away_team) or (None, None) if not parseable.
        """
        separators = [
            (" at ",  "american"),  # away at home  (American sports)
            (" vs. ", "vs"),        # home vs. away
            (" vs ",  "vs"),        # home vs away
            (" v ",   "vs"),        # home v away
            (" @ ",   "american"),  # away @ home   (some formats)
        ]
        for sep, fmt in separators:
            if sep in title:
                left, right = title.split(sep, 1)
                left, right = left.strip(), right.strip()
                if not left or not right:
                    continue
                if fmt == "american":
                    return right, left    # home, away
                else:
                    return left, right   # home, away
        return None, None

    # ── Selection normalisation ───────────────────────────────────────────────

    @classmethod
    def normalize_selection(
        cls,
        yes_sub_title: str,
        home_team: str,
        away_team: str,
        market_key: str = "h2h",
    ) -> str:
        """
        Normalise a Kalshi market's yes_sub_title to our internal selection key.

        Args:
            yes_sub_title: The team/outcome label from Kalshi (e.g. "Washington").
            home_team:      Home team from the event title.
            away_team:      Away team from the event title.
            market_key:     Internal market key (h2h, spreads, totals).

        Returns:
            'home', 'away', 'draw', 'over', 'under', or the raw yes_sub_title.
        """
        t = yes_sub_title.lower().strip()

        if market_key == "totals":
            if t.startswith("over") or t == "o":
                return "over"
            if t.startswith("under") or t == "u":
                return "under"
            return yes_sub_title

        if market_key in ("h2h", "spreads"):
            if t in ("draw", "tie"):
                return "draw"
            # Fuzzy match team names
            home_score = token_sort_ratio(yes_sub_title, home_team)
            away_score = token_sort_ratio(yes_sub_title, away_team)
            if home_score >= 0.6 and home_score >= away_score:
                return "home"
            if away_score >= 0.6 and away_score > home_score:
                return "away"

        return yes_sub_title

    # ── Market title pattern matching ─────────────────────────────────────────

    @classmethod
    def from_kalshi_title(cls, title: str) -> Optional[str]:
        """
        Infer internal market key from a Kalshi market title string.

        Used for KalshiMarketType.from_kalshi_title() in tests and for markets
        whose type cannot be determined from the series ticker alone.

        Examples:
          "Utah at Washington Winner?"   → "h2h"
          "Will total points be over 216.5?"  → "totals"
          "Will Utah cover -4.5?"        → "spreads"
          "Some unrelated market"        → None
        """
        t = title.lower().strip()

        # Totals: explicit over/under with a number
        if re.search(r'\b(over|under)\b', t) and re.search(r'\d', t):
            return "totals"

        # Spreads: a +/- number (handicap / point spread)
        if re.search(r'[+-]\d+\.?\d*', t):
            return "spreads"

        # Draw / Tie
        if "draw" in t or " tie " in t:
            return "h2h"

        # Explicit winner language
        if re.search(r'\b(winner|win|wins|to win|qualify)\b', t):
            return "h2h"

        # Kalshi game-winner markets end with "Winner?"
        if t.endswith("winner?") or t.endswith("winner"):
            return "h2h"

        # Short titles (≤ 4 words) with no special keywords = team name → h2h
        if t and len(t.split()) <= 4 and not any(
            kw in t for kw in ("total", "spread", "over", "under", "point")
        ):
            return "h2h"

        return None
