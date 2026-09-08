#!/usr/bin/env python3
"""Client for Sleeper's undocumented projections/stats API (api.sleeper.com),
scored against each league's actual scoring_settings.

This is a separate host from the documented api.sleeper.app/v1 API used
elsewhere in this server, and it is not officially documented - it can
change shape or disappear without notice. Every function here fails
gracefully to an empty dict (with a logged warning) rather than raising,
so a broken response here degrades projections instead of breaking a tool.

Endpoints (all confirmed live 2026-09-07):
    GET /projections/nfl/{season}/{week}?season_type=regular&position[]=QB&...
    GET /stats/nfl/{season}/{week}?season_type=regular&position[]=QB&...  (actuals)

Both return a flat JSON list covering every requested position in one call,
one entry per player, shaped like:
    {"player_id": "4046", "week": 1, "season": "2026",
     "stats": {"pts_ppr": 24.5, "pass_yd": 275.0, "pass_td": 1.9, ...}, ...}

`stats` carries Sleeper's own generic-PPR point total (pts_ppr) *and* the
raw component stats (pass_yd, rush_td, rec, fgm_40_49, pts_allow_7_13, ...).
Sleeper's league `scoring_settings` (from /league/{league_id}, see
get_league_scoring_settings) use that same stat vocabulary as keys, so a
league-accurate projection is sum(stat_value * scoring_settings[stat_key])
over whichever keys exist in both - which matters most for K/DEF, where the
generic pts_ppr numbers Sleeper exposes for other platforms routinely
disagree with a league's actual field-goal-distance or points/yards-allowed
rules.

A full week for all six skill positions is ~2MB of JSON; only each player's
`stats` dict is kept - the rest (player metadata, dates, etc.) is discarded
rather than cached, to stay light on the 512MB Render instance.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, Set, Tuple

import httpx

from cache_backend import get_cache_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.com"
FANTASY_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]

# Undocumented endpoint, and rosters/projections don't move faster than this
CACHE_TTL_SECONDS = 4 * 60 * 60

# Scoring settings change essentially never mid-season, but this keeps a
# stale commissioner edit from sticking around forever.
SCORING_SETTINGS_CACHE_TTL_SECONDS = 24 * 60 * 60

# Sums for ros_projected run through this week (inclusive) if not told otherwise
REGULAR_SEASON_LAST_WEEK = 18

# Dedup key-mismatch warnings per (league_id, gap signature) so a stat/scoring
# vocabulary mismatch is logged once, not on every single request.
_logged_mapping_gaps: Set[Tuple[str, frozenset, frozenset]] = set()


def _fetch_stats_map(kind: str, season: str, week: int) -> Dict[str, Dict[str, float]]:
    """Fetch one week of projections ("proj") or actuals ("stat") for all
    skill positions in a single call, returning {player_id: {stat_key: value}}
    - the full projected/actual stat line per player, not just pts_ppr.

    Cached for CACHE_TTL_SECONDS. Returns {} (logged) on any failure -
    network error, non-2xx, or an unexpected shape - never raises.
    """
    cache = get_cache_client()
    cache_key = f"sleeper_projections:{kind}:{season}:{week}"

    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return json.loads(cached)
    except Exception as e:
        logger.warning(
            f"Error reading Sleeper projections cache (key={cache_key}): {e}"
        )

    path = "projections" if kind == "proj" else "stats"
    url = f"{BASE_URL}/{path}/nfl/{season}/{week}"
    params = [("season_type", "regular")] + [
        ("position[]", p) for p in FANTASY_POSITIONS
    ]

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        logger.warning(
            f"Sleeper {kind} projections request failed (url={url}, season={season}, "
            f"week={week}, error_type={type(e).__name__}, error_message={str(e)})"
        )
        return {}

    if not isinstance(data, list):
        logger.warning(
            f"Unexpected response shape from undocumented Sleeper {kind} endpoint "
            f"({url}): expected a list, got {type(data).__name__}. First 500 chars: "
            f"{str(data)[:500]!r}"
        )
        return {}

    stats_map: Dict[str, Dict[str, float]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        player_id = entry.get("player_id")
        stats = entry.get("stats")
        if not player_id or not isinstance(stats, dict):
            continue

        clean_stats: Dict[str, float] = {}
        for stat_key, value in stats.items():
            try:
                clean_stats[stat_key] = float(value)
            except (TypeError, ValueError):
                continue

        if clean_stats:
            stats_map[str(player_id)] = clean_stats

    try:
        cache.set(cache_key, json.dumps(stats_map), ex=CACHE_TTL_SECONDS)
    except Exception as e:
        logger.warning(
            f"Error writing Sleeper projections cache (key={cache_key}): {e}"
        )

    return stats_map


def get_weekly_projected_stats(season: str, week: int) -> Dict[str, Dict[str, float]]:
    """{player_id: {stat_key: projected value}} for one week, all skill positions."""
    return _fetch_stats_map("proj", season, week)


def get_weekly_actual_stats(season: str, week: int) -> Dict[str, Dict[str, float]]:
    """{player_id: {stat_key: actual value}} for one week, all skill positions."""
    return _fetch_stats_map("stat", season, week)


def get_league_scoring_settings(league_id: str, base_url: str) -> Dict[str, float]:
    """Fetch and cache a league's scoring_settings map (stat_key -> points).

    Cached per league_id for SCORING_SETTINGS_CACHE_TTL_SECONDS, so scoring
    a whole roster or waiver-wire pull only fetches this once, not once per
    player. Returns {} (logged) on any failure.
    """
    cache = get_cache_client()
    cache_key = f"sleeper_projections:scoring_settings:{league_id}"

    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return json.loads(cached)
    except Exception as e:
        logger.warning(
            f"Error reading league scoring settings cache (key={cache_key}): {e}"
        )

    url = f"{base_url}/league/{league_id}"
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        logger.warning(
            f"Failed to fetch league scoring settings (url={url}, "
            f"error_type={type(e).__name__}, error_message={str(e)})"
        )
        return {}

    if not isinstance(data, dict) or not isinstance(data.get("scoring_settings"), dict):
        logger.warning(
            f"Unexpected league info response shape from {url}: expected an object "
            f"with a scoring_settings dict, got {type(data).__name__}. First 500 "
            f"chars: {str(data)[:500]!r}"
        )
        return {}

    scoring_settings: Dict[str, float] = {}
    for stat_key, weight in data["scoring_settings"].items():
        try:
            scoring_settings[stat_key] = float(weight)
        except (TypeError, ValueError):
            continue

    try:
        cache.set(
            cache_key,
            json.dumps(scoring_settings),
            ex=SCORING_SETTINGS_CACHE_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning(
            f"Error writing league scoring settings cache (key={cache_key}): {e}"
        )

    return scoring_settings


def _score_stats(
    stats: Dict[str, float], scoring_settings: Dict[str, float]
) -> Tuple[float, Set[str]]:
    """sum(stat_value * scoring_settings[stat_key]) over keys present in both.

    Returns (total_points, matched_stat_keys) - the matched-key set feeds
    the unmapped-key diagnostics in _log_mapping_gaps.
    """
    total = 0.0
    matched: Set[str] = set()
    for stat_key, value in stats.items():
        weight = scoring_settings.get(stat_key)
        if weight is not None:
            total += value * weight
            matched.add(stat_key)
    return round(total, 2), matched


def _log_mapping_gaps(
    league_id: str,
    all_stat_keys: Set[str],
    scoring_settings: Dict[str, float],
    matched_keys: Set[str],
) -> None:
    """Log (once per distinct gap, per league) any stat keys Sleeper projected
    that this league's scoring_settings has no rule for, and any scoring
    rule that never matched a projected stat key. Silent zeros - a stat
    landing in neither set and just not contributing - are exactly the
    failure mode this exists to surface.
    """
    unscored_stat_keys = all_stat_keys - set(scoring_settings.keys())
    unused_scoring_keys = set(scoring_settings.keys()) - all_stat_keys

    if not unscored_stat_keys and not unused_scoring_keys:
        return

    signature = (
        league_id,
        frozenset(unscored_stat_keys),
        frozenset(unused_scoring_keys),
    )
    if signature in _logged_mapping_gaps:
        return
    _logged_mapping_gaps.add(signature)

    logger.warning(
        f"League {league_id} scoring/stat key mismatch - "
        f"projected stat keys with no scoring rule (not scored): "
        f"{sorted(unscored_stat_keys)}; "
        f"scoring rules that matched no projected stat key this call: "
        f"{sorted(unused_scoring_keys)}. Matched {len(matched_keys)} keys fine."
    )


def _scored_points_for_week(
    season: str, week: int, kind: str, league_id: str, base_url: str
) -> Dict[str, Dict[str, float]]:
    """{player_id: {"scored": league-accurate points, "generic": Sleeper's pts_ppr}}
    for one week of projections ("proj") or actuals ("stat")."""
    stats_map = _fetch_stats_map(kind, season, week)
    scoring_settings = get_league_scoring_settings(league_id, base_url)

    if not scoring_settings:
        logger.warning(
            f"No scoring settings available for league {league_id}; cannot compute "
            f"league-accurate {kind} projections for week {week}"
        )
        return {}

    all_stat_keys: Set[str] = set()
    matched_keys: Set[str] = set()
    results: Dict[str, Dict[str, float]] = {}

    for player_id, stats in stats_map.items():
        all_stat_keys.update(stats.keys())
        scored, matched = _score_stats(stats, scoring_settings)
        matched_keys.update(matched)
        results[player_id] = {
            "scored": scored,
            "generic": round(stats.get("pts_ppr", 0.0), 2),
        }

    if all_stat_keys:
        _log_mapping_gaps(league_id, all_stat_keys, scoring_settings, matched_keys)

    return results


def get_weekly_scored_points(
    season: str, week: int, league_id: str, base_url: str
) -> Dict[str, Dict[str, float]]:
    """{player_id: {"scored": ..., "generic": ...}} projected points for one week."""
    return _scored_points_for_week(season, week, "proj", league_id, base_url)


def get_weekly_actual_scored_points(
    season: str, week: int, league_id: str, base_url: str
) -> Dict[str, Dict[str, float]]:
    """{player_id: {"scored": ..., "generic": ...}} actual points for one week."""
    return _scored_points_for_week(season, week, "stat", league_id, base_url)


def get_ros_scored_points(
    season: str,
    current_week: int,
    league_id: str,
    base_url: str,
    last_week: int = REGULAR_SEASON_LAST_WEEK,
) -> Dict[str, Dict[str, float]]:
    """{player_id: {"scored": ..., "generic": ...}} summed from current_week
    through last_week.

    Scoring is a fixed per-stat weighted sum, so summing each week's scored
    total is equivalent to summing raw stats across weeks and scoring once -
    and reuses the same per-week cache as get_weekly_scored_points. Weeks
    are fetched concurrently (bounded pool) so a cold cache isn't an ~18-call
    serial wait.
    """
    weeks = range(current_week, last_week + 1)
    totals: Dict[str, float] = {}
    generic_totals: Dict[str, float] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        week_results = pool.map(
            lambda week: get_weekly_scored_points(season, week, league_id, base_url),
            weeks,
        )
        for week_result in week_results:
            for player_id, points in week_result.items():
                totals[player_id] = totals.get(player_id, 0.0) + points["scored"]
                generic_totals[player_id] = (
                    generic_totals.get(player_id, 0.0) + points["generic"]
                )

    return {
        player_id: {
            "scored": round(totals[player_id], 2),
            "generic": round(generic_totals.get(player_id, 0.0), 2),
        }
        for player_id in totals
    }


def overlay_projections(
    players: Dict[str, Dict[str, Any]],
    player_ids: Iterable[str],
    season: str,
    week: int,
    league_id: str,
    base_url: str,
) -> None:
    """Fill in stats.projected/actual/ros_projected for player_ids still null,
    scored against `league_id`'s actual scoring_settings.

    Mutates `players` in place. Only ever fills a field that is currently
    None/missing - never overwrites data the player cache already provided
    (e.g. from Fantasy Nerds) - so this is purely a fallback for whatever the
    existing pipeline didn't populate. Each filled entry carries both
    `fantasy_points` (league-scored) and `fantasy_points_generic` (Sleeper's
    platform-generic pts_ppr), so the two can be compared. There is no
    fantasy_points_low/high - Sleeper's projections endpoint doesn't expose a
    real range, and copying fantasy_points into both would be misleading.

    Args:
        players: {player_id: player_dict} - typically the full cached
            player pool, or the subset you already have on hand.
        player_ids: which of those player_ids to fill in (e.g. a roster's
            players, or a page of waiver-wire candidates).
        season: current season, as a string (e.g. "2026")
        week: current week
        league_id: the Sleeper league to score against
        base_url: the documented Sleeper API base (https://api.sleeper.app/v1)
            used to fetch this league's scoring_settings
    """
    player_ids = [pid for pid in player_ids if pid and pid in players]
    if not player_ids:
        return

    try:
        weekly_proj = get_weekly_scored_points(season, week, league_id, base_url)
        weekly_actual = get_weekly_actual_scored_points(
            season, week, league_id, base_url
        )
        ros_proj = get_ros_scored_points(season, week, league_id, base_url)
    except Exception as e:
        logger.warning(
            f"Sleeper projections overlay failed, continuing without it: {e}"
        )
        return

    for player_id in player_ids:
        player_data = players[player_id]
        stats = player_data.setdefault(
            "stats", {"projected": None, "actual": None, "ros_projected": None}
        )

        if stats.get("projected") is None and player_id in weekly_proj:
            points = weekly_proj[player_id]
            stats["projected"] = {
                "fantasy_points": points["scored"],
                "fantasy_points_generic": points["generic"],
                "source": "sleeper_projections",
            }

        if stats.get("actual") is None and player_id in weekly_actual:
            points = weekly_actual[player_id]
            stats["actual"] = {
                "fantasy_points": points["scored"],
                "fantasy_points_generic": points["generic"],
                "game_stats": None,
                "game_status": "final",
                "source": "sleeper_projections",
            }

        if stats.get("ros_projected") is None and player_id in ros_proj:
            points = ros_proj[player_id]
            stats["ros_projected"] = {
                "fantasy_points": points["scored"],
                "fantasy_points_generic": points["generic"],
                "season": str(season),
                "source": "sleeper_projections",
            }
