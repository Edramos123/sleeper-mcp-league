"""Unit tests for sleeper_projections_client.py.

These test the undocumented-endpoint parsing, league-scoring computation,
mapping-gap diagnostics, and overlay merge behavior, without hitting the
real network - httpx.Client.get is mocked directly, matching this repo's
existing style of mocking at the boundary rather than using a dedicated
HTTP-mocking library.
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


@pytest.fixture(autouse=True)
def clear_mapping_gap_log():
    spc._logged_mapping_gaps.clear()
    yield
    spc._logged_mapping_gaps.clear()


def _mock_response(json_data, status_code=200):
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_data
    response.raise_for_status.return_value = None
    return response


BASE_URL = "https://api.sleeper.app/v1"


class TestFetchStatsMap:
    def test_well_formed_response_extracts_full_stat_line(self):
        data = [
            {
                "player_id": "4046",
                "stats": {"pts_ppr": 24.567, "pass_yd": 275.0, "pass_td": 1.9},
            },
        ]
        with patch.object(httpx.Client, "get", return_value=_mock_response(data)):
            result = spc.get_weekly_projected_stats("2026", 1)

        assert result == {"4046": {"pts_ppr": 24.567, "pass_yd": 275.0, "pass_td": 1.9}}

    def test_skips_malformed_entries_without_crashing(self):
        data = [
            "not a dict",
            {"player_id": None, "stats": {"pts_ppr": 5}},
            {"player_id": "999", "stats": "not a dict"},
            {"player_id": "111", "stats": {"pts_ppr": 12.3, "bad": "not a number"}},
        ]
        with patch.object(httpx.Client, "get", return_value=_mock_response(data)):
            result = spc.get_weekly_projected_stats("2026", 1)

        assert result == {"111": {"pts_ppr": 12.3}}

    def test_non_list_response_returns_empty_dict(self):
        with patch.object(
            httpx.Client, "get", return_value=_mock_response({"message": "Forbidden"})
        ):
            result = spc.get_weekly_projected_stats("2026", 1)

        assert result == {}

    def test_http_error_returns_empty_dict_not_raise(self):
        with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("boom")):
            result = spc.get_weekly_projected_stats("2026", 1)

        assert result == {}

    def test_cache_hit_avoids_second_network_call(self):
        data = [{"player_id": "4046", "stats": {"pts_ppr": 20.0}}]
        with patch.object(
            httpx.Client, "get", return_value=_mock_response(data)
        ) as mock_get:
            spc.get_weekly_projected_stats("2026", 1)
            spc.get_weekly_projected_stats("2026", 1)

        assert mock_get.call_count == 1

    def test_actuals_use_stats_path(self):
        data = [{"player_id": "4046", "stats": {"pts_ppr": 18.4}}]
        with patch.object(
            httpx.Client, "get", return_value=_mock_response(data)
        ) as mock_get:
            spc.get_weekly_actual_stats("2026", 1)

        called_url = mock_get.call_args.args[0]
        assert "/stats/nfl/2026/1" in called_url


class TestLeagueScoringSettings:
    def test_extracts_and_coerces_scoring_settings(self):
        data = {"scoring_settings": {"pass_td": 4, "rec": "1.0", "bonus_x": "n/a"}}
        with patch.object(httpx.Client, "get", return_value=_mock_response(data)):
            result = spc.get_league_scoring_settings("999", BASE_URL)

        assert result == {"pass_td": 4.0, "rec": 1.0}

    def test_non_dict_response_returns_empty(self):
        with patch.object(
            httpx.Client, "get", return_value=_mock_response(["not", "a", "dict"])
        ):
            result = spc.get_league_scoring_settings("999", BASE_URL)
        assert result == {}

    def test_missing_scoring_settings_key_returns_empty(self):
        with patch.object(
            httpx.Client, "get", return_value=_mock_response({"name": "My League"})
        ):
            result = spc.get_league_scoring_settings("999", BASE_URL)
        assert result == {}

    def test_cached_per_league(self):
        data = {"scoring_settings": {"rec": 1.0}}
        with patch.object(
            httpx.Client, "get", return_value=_mock_response(data)
        ) as mock_get:
            spc.get_league_scoring_settings("999", BASE_URL)
            spc.get_league_scoring_settings("999", BASE_URL)

        assert mock_get.call_count == 1


class TestScoreStats:
    def test_multiplies_and_sums_matched_keys_only(self):
        stats = {"pass_yd": 275.0, "pass_td": 1.9, "unmatched_key": 5.0}
        scoring = {"pass_yd": 0.04, "pass_td": 4.0}
        total, matched = spc._score_stats(stats, scoring)

        assert total == round(275.0 * 0.04 + 1.9 * 4.0, 2)
        assert matched == {"pass_yd", "pass_td"}

    def test_defense_tiered_scoring(self):
        # Sanity check pattern from the DEF pts_allow_* one-hot tiers
        stats = {"sack": 2.3, "int": 1.1, "fum_rec": 0.8, "pts_allow_1_6": 1.0}
        scoring = {
            "sack": 1,
            "int": 2,
            "fum_rec": 2,
            "pts_allow_0": 5,
            "pts_allow_1_6": 4,
        }
        total, matched = spc._score_stats(stats, scoring)

        expected = 2.3 * 1 + 1.1 * 2 + 0.8 * 2 + 1.0 * 4
        assert total == round(expected, 2)
        assert "pts_allow_0" not in matched


class TestGetWeeklyScoredPoints:
    def test_scores_against_league_and_reports_generic(self):
        proj_data = [
            {
                "player_id": "TEN",
                "stats": {"pts_ppr": 7.13, "sack": 2.0, "int": 1.0, "fum_rec": 0.5},
            }
        ]
        league_data = {"scoring_settings": {"sack": 1.0, "int": 2.0, "fum_rec": 2.0}}

        def fake_get(url, *args, **kwargs):
            if "sleeper.com" in url:
                return _mock_response(proj_data)
            return _mock_response(league_data)

        with patch.object(httpx.Client, "get", side_effect=fake_get):
            result = spc.get_weekly_scored_points("2026", 1, "999", BASE_URL)

        expected_scored = round(2.0 * 1.0 + 1.0 * 2.0 + 0.5 * 2.0, 2)
        assert result == {"TEN": {"scored": expected_scored, "generic": 7.13}}
        # Must actually differ from the generic Sleeper number, or the
        # scoring function silently isn't being applied.
        assert result["TEN"]["scored"] != result["TEN"]["generic"]

    def test_no_scoring_settings_returns_empty_and_does_not_crash(self):
        proj_data = [{"player_id": "TEN", "stats": {"pts_ppr": 7.13}}]

        def fake_get(url, *args, **kwargs):
            if "sleeper.com" in url:
                return _mock_response(proj_data)
            return _mock_response({"name": "no scoring_settings key"})

        with patch.object(httpx.Client, "get", side_effect=fake_get):
            result = spc.get_weekly_scored_points("2026", 1, "999", BASE_URL)

        assert result == {}

    def test_logs_mapping_gaps_once_per_signature(self, caplog):
        proj_data = [
            {"player_id": "1", "stats": {"pts_ppr": 1.0, "totally_unmapped_key": 3.0}}
        ]
        league_data = {"scoring_settings": {"pass_td": 4.0, "never_seen_key": 1.0}}

        def fake_get(url, *args, **kwargs):
            if "sleeper.com" in url:
                return _mock_response(proj_data)
            return _mock_response(league_data)

        with patch.object(httpx.Client, "get", side_effect=fake_get):
            with caplog.at_level("WARNING"):
                spc.get_weekly_scored_points("2026", 1, "999", BASE_URL)
                spc.get_weekly_scored_points("2026", 1, "999", BASE_URL)

        gap_warnings = [
            r for r in caplog.records if "scoring/stat key mismatch" in r.message
        ]
        assert len(gap_warnings) == 1
        assert "totally_unmapped_key" in gap_warnings[0].message
        assert "never_seen_key" in gap_warnings[0].message


class TestGetRosScoredPoints:
    def test_sums_scored_and_generic_across_remaining_weeks(self):
        def fake_weekly(season, week, league_id, base_url):
            return {"4046": {"scored": float(week) * 2, "generic": float(week)}}

        with patch.object(spc, "get_weekly_scored_points", side_effect=fake_weekly):
            result = spc.get_ros_scored_points(
                "2026", current_week=1, league_id="999", base_url=BASE_URL, last_week=3
            )

        assert result == {"4046": {"scored": 12.0, "generic": 6.0}}  # (1+2+3)*2, 1+2+3


class TestOverlayProjections:
    def test_fills_only_missing_fields_with_scored_and_generic(self):
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
                spc,
                "get_weekly_scored_points",
                return_value={
                    "1": {"scored": 10.0, "generic": 8.0},
                    "2": {"scored": 20.0, "generic": 15.0},
                },
            ),
            patch.object(spc, "get_weekly_actual_scored_points", return_value={}),
            patch.object(
                spc,
                "get_ros_scored_points",
                return_value={
                    "1": {"scored": 150.0, "generic": 120.0},
                    "2": {"scored": 300.0, "generic": 250.0},
                },
            ),
        ):
            spc.overlay_projections(players, players.keys(), "2026", 1, "999", BASE_URL)

        # Player 1 had nothing - filled with both scored (fantasy_points) and generic
        assert players["1"]["stats"]["projected"]["fantasy_points"] == 10.0
        assert players["1"]["stats"]["projected"]["fantasy_points_generic"] == 8.0
        assert "fantasy_points_low" not in players["1"]["stats"]["projected"]
        assert "fantasy_points_high" not in players["1"]["stats"]["projected"]
        assert players["1"]["stats"]["ros_projected"]["fantasy_points"] == 150.0

        # Player 2 already had a real projected value - must not be overwritten
        assert players["2"]["stats"]["projected"]["fantasy_points"] == 99.0
        # ...but ros_projected was still null, so that one does get filled
        assert players["2"]["stats"]["ros_projected"]["fantasy_points"] == 300.0

    def test_missing_player_ids_are_ignored(self):
        players = {"1": {"stats": {"projected": None}}}
        with patch.object(
            spc,
            "get_weekly_scored_points",
            return_value={"999": {"scored": 10.0, "generic": 10.0}},
        ):
            spc.overlay_projections(players, ["1", "999"], "2026", 1, "999", BASE_URL)
        assert players["1"]["stats"]["projected"] is None

    def test_overlay_failure_degrades_silently(self):
        players = {"1": {"stats": {"projected": None}}}
        with patch.object(
            spc, "get_weekly_scored_points", side_effect=RuntimeError("boom")
        ):
            spc.overlay_projections(players, ["1"], "2026", 1, "999", BASE_URL)
        assert players["1"]["stats"]["projected"] is None
