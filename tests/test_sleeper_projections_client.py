"""Unit tests for sleeper_projections_client.py.

These test the undocumented-endpoint parsing/graceful-degradation logic and
the overlay_projections merge behavior, without hitting the real network -
httpx.Client.get is mocked directly, matching this repo's existing style of
mocking at the boundary rather than using a dedicated HTTP-mocking library.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

import sleeper_projections_client as spc


class FakeCache:
    """Minimal in-memory stand-in for cache_backend's cache client."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True


@pytest.fixture(autouse=True)
def fake_cache(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(spc, "get_cache_client", lambda: cache)
    return cache


def _mock_response(json_data, status_code=200):
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_data
    response.raise_for_status.return_value = None
    return response


class TestFetchPtsPprMap:
    def test_well_formed_response_extracts_pts_ppr(self):
        data = [
            {"player_id": "4046", "stats": {"pts_ppr": 24.567}},
            {"player_id": "1234", "stats": {"pts_ppr": 10}},
        ]
        with patch.object(httpx.Client, "get", return_value=_mock_response(data)):
            result = spc.get_weekly_projected_points("2026", 1)

        assert result == {"4046": 24.57, "1234": 10.0}

    def test_skips_malformed_entries_without_crashing(self):
        data = [
            "not a dict",
            {"player_id": None, "stats": {"pts_ppr": 5}},
            {"player_id": "999", "stats": "not a dict"},
            {"player_id": "888", "stats": {"pts_ppr": None}},
            {"player_id": "777", "stats": {"pts_ppr": "not a number"}},
            {"player_id": "111", "stats": {"pts_ppr": 12.3}},
        ]
        with patch.object(httpx.Client, "get", return_value=_mock_response(data)):
            result = spc.get_weekly_projected_points("2026", 1)

        assert result == {"111": 12.3}

    def test_non_list_response_returns_empty_dict(self):
        with patch.object(
            httpx.Client, "get", return_value=_mock_response({"message": "Forbidden"})
        ):
            result = spc.get_weekly_projected_points("2026", 1)

        assert result == {}

    def test_http_error_returns_empty_dict_not_raise(self):
        with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("boom")):
            result = spc.get_weekly_projected_points("2026", 1)

        assert result == {}

    def test_cache_hit_avoids_second_network_call(self, fake_cache):
        data = [{"player_id": "4046", "stats": {"pts_ppr": 20.0}}]
        with patch.object(
            httpx.Client, "get", return_value=_mock_response(data)
        ) as mock_get:
            spc.get_weekly_projected_points("2026", 1)
            spc.get_weekly_projected_points("2026", 1)

        assert mock_get.call_count == 1

    def test_actuals_use_stats_path(self):
        data = [{"player_id": "4046", "stats": {"pts_ppr": 18.4}}]
        with patch.object(
            httpx.Client, "get", return_value=_mock_response(data)
        ) as mock_get:
            spc.get_weekly_actual_points("2026", 1)

        called_url = mock_get.call_args.args[0]
        assert "/stats/nfl/2026/1" in called_url


class TestGetRosProjectedPoints:
    def test_sums_across_remaining_weeks(self):
        def fake_weekly(season, week):
            return {"4046": float(week)}

        with patch.object(spc, "get_weekly_projected_points", side_effect=fake_weekly):
            result = spc.get_ros_projected_points("2026", current_week=1, last_week=3)

        assert result == {"4046": 6.0}  # 1 + 2 + 3


class TestOverlayProjections:
    def test_fills_only_missing_fields(self):
        players = {
            "1": {
                "position": "RB",
                "stats": {"projected": None, "actual": None, "ros_projected": None},
            },
            "2": {
                "position": "WR",
                "stats": {
                    "projected": {"fantasy_points": 99.0},
                    "actual": None,
                    "ros_projected": None,
                },
            },
        }

        with (
            patch.object(
                spc, "get_weekly_projected_points", return_value={"1": 10.0, "2": 20.0}
            ),
            patch.object(spc, "get_weekly_actual_points", return_value={}),
            patch.object(
                spc, "get_ros_projected_points", return_value={"1": 150.0, "2": 300.0}
            ),
        ):
            spc.overlay_projections(players, players.keys(), "2026", 1)

        # Player 1 had nothing - both projected and ros_projected filled in
        assert players["1"]["stats"]["projected"]["fantasy_points"] == 10.0
        assert players["1"]["stats"]["ros_projected"]["fantasy_points"] == 150.0

        # Player 2 already had a real projected value - must not be overwritten
        assert players["2"]["stats"]["projected"]["fantasy_points"] == 99.0
        # ...but ros_projected was still null, so that one does get filled
        assert players["2"]["stats"]["ros_projected"]["fantasy_points"] == 300.0

    def test_missing_player_ids_are_ignored(self):
        players = {"1": {"stats": {"projected": None}}}
        with patch.object(
            spc, "get_weekly_projected_points", return_value={"999": 10.0}
        ):
            # Should not raise even though "999" isn't in `players`
            spc.overlay_projections(players, ["1", "999"], "2026", 1)
        assert players["1"]["stats"]["projected"] is None

    def test_overlay_failure_degrades_silently(self):
        players = {"1": {"stats": {"projected": None}}}
        with patch.object(
            spc, "get_weekly_projected_points", side_effect=RuntimeError("boom")
        ):
            spc.overlay_projections(players, ["1"], "2026", 1)
        assert players["1"]["stats"]["projected"] is None
