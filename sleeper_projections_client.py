#!/usr/bin/env python3
"""Client for Sleeper's undocumented projections/stats API (api.sleeper.com).

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
     "stats": {"pts_ppr": 24.5, ...many other stat fields...}, ...}

A full week for all six skill positions is ~2MB of JSON. Only
{player_id: pts_ppr} is kept from it - the rest is discarded rather than
cached, to stay light on the 512MB Render instance.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable

import httpx

from cache_backend import get_cache_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.com"
FANTASY_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]

# Undocumented endpoint, and rosters/projections don't move faster than this
CACHE_TTL_SECONDS = 4 * 60 * 60

# Sums for ros_projected run through this week (inclusive) if not told otherwise
REGULAR_SEASON_LAST_WEEK = 18


def _fetch_pts_ppr_map(kind: str, season: str, week: int) -> Dict[str, float]:
    """Fetch one week of projections ("proj") or actuals ("stat") for all
    skill positions in a single call, returning {player_id: pts_ppr}.

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

    pts_map: Dict[str, float] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        player_id = entry.get("player_id")
        stats = entry.get("stats")
        if not player_id or not isinstance(stats, dict):
            continue
        pts_ppr = stats.get("pts_ppr")
        if pts_ppr is None:
            continue
        try:
            pts_map[str(player_id)] = round(float(pts_ppr), 2)
        except (TypeError, ValueError):
            continue

    try:
        cache.set(cache_key, json.dumps(pts_map), ex=CACHE_TTL_SECONDS)
    except Exception as e:
        logger.warning(
            f"Error writing Sleeper projections cache (key={cache_key}): {e}"
        )

    return pts_map


def get_weekly_projected_points(season: str, week: int) -> Dict[str, float]:
    """{player_id: projected PPR fantasy points} for one week, all skill positions."""
    return _fetch_pts_ppr_map("proj", season, week)


def get_weekly_actual_points(season: str, week: int) -> Dict[str, float]:
    """{player_id: actual PPR fantasy points} for one week, all skill positions."""
    return _fetch_pts_ppr_map("stat", season, week)


def get_ros_projected_points(
    season: str, current_week: int, last_week: int = REGULAR_SEASON_LAST_WEEK
) -> Dict[str, float]:
    """{player_id: sum of projected PPR points from current_week through last_week}.

    Sums each remaining week's (individually cached) weekly projections
    rather than relying on Sleeper's separate no-week "season projections"
    variant, whose semantics (full-season vs. remaining) aren't confirmed.

    Each week is fetched from its own cache entry, so this is only slow
    (up to ~18 requests) the first time a given week is needed; every
    subsequent call this cache period is instant. Weeks are fetched
    concurrently (bounded pool) to keep that first cold call reasonable.
    """
    weeks = range(current_week, last_week + 1)
    totals: Dict[str, float] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        week_maps = pool.map(
            lambda week: get_weekly_projected_points(season, week), weeks
        )
        for week_map in week_maps:
            for player_id, pts in week_map.items():
                totals[player_id] = totals.get(player_id, 0.0) + pts

    return {player_id: round(pts, 2) for player_id, pts in totals.items()}


def overlay_projections(
    players: Dict[str, Dict[str, Any]],
    player_ids: Iterable[str],
    season: str,
    week: int,
) -> None:
    """Fill in stats.projected/actual/ros_projected for player_ids still null.

    Mutates `players` in place. Only ever fills a field that is currently
    None/missing - never overwrites data the player cache already provided
    (e.g. from Fantasy Nerds), so this is purely a fallback for whatever the
    existing pipeline didn't populate.

    Args:
        players: {player_id: player_dict} - typically the full cached
            player pool, or the subset you already have on hand.
        player_ids: which of those player_ids to fill in (e.g. a roster's
            players, or a page of waiver-wire candidates).
        season: current season, as a string (e.g. "2026")
        week: current week
    """
    player_ids = [pid for pid in player_ids if pid and pid in players]
    if not player_ids:
        return

    try:
        weekly_proj = get_weekly_projected_points(season, week)
        weekly_actual = get_weekly_actual_points(season, week)
        ros_proj = get_ros_projected_points(season, week)
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
            pts = weekly_proj[player_id]
            stats["projected"] = {
                "fantasy_points": pts,
                "fantasy_points_low": pts,
                "fantasy_points_high": pts,
                "source": "sleeper_projections",
            }

        if stats.get("actual") is None and player_id in weekly_actual:
            stats["actual"] = {
                "fantasy_points": weekly_actual[player_id],
                "game_stats": None,
                "game_status": "final",
                "source": "sleeper_projections",
            }

        if stats.get("ros_projected") is None and player_id in ros_proj:
            stats["ros_projected"] = {
                "fantasy_points": ros_proj[player_id],
                "season": str(season),
                "source": "sleeper_projections",
            }
