import json
import pathlib
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from scrapers.weather import (
    _aggregate_to_monthly,
    _derive_best_months,
    _merge_sea_temps,
    scrape_weather,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class TestAggregation(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((FIXTURES / "silba_weather.json").read_text())
        from datetime import date
        self.start = date(2022, 1, 1)
        self.end = date(2024, 12, 26)

    def test_produces_12_monthly_entries(self):
        monthly = _aggregate_to_monthly(self.fixture, self.start, self.end)
        self.assertEqual(len(monthly), 12)

    def test_month_numbers_correct(self):
        monthly = _aggregate_to_monthly(self.fixture, self.start, self.end)
        months = [m["month"] for m in monthly]
        self.assertEqual(months, list(range(1, 13)))

    def test_july_warmer_than_january(self):
        monthly = _aggregate_to_monthly(self.fixture, self.start, self.end)
        jan = next(m for m in monthly if m["month"] == 1)
        jul = next(m for m in monthly if m["month"] == 7)
        self.assertGreater(jul["avg_temp_c"], jan["avg_temp_c"])

    def test_july_flagged_as_peak_season(self):
        monthly = _aggregate_to_monthly(self.fixture, self.start, self.end)
        for m in monthly:
            if m.get("avg_temp_c") is not None and m.get("avg_rain_days") is not None:
                m["peak_season"] = m["avg_temp_c"] > 24.0 and m["avg_rain_days"] < 5
        jul = next(m for m in monthly if m["month"] == 7)
        self.assertTrue(jul["peak_season"])

    def test_january_not_flagged_as_peak_season(self):
        monthly = _aggregate_to_monthly(self.fixture, self.start, self.end)
        for m in monthly:
            if m.get("avg_temp_c") is not None and m.get("avg_rain_days") is not None:
                m["peak_season"] = m["avg_temp_c"] > 24.0 and m["avg_rain_days"] < 5
        jan = next(m for m in monthly if m["month"] == 1)
        self.assertFalse(jan["peak_season"])

    def test_sunshine_in_hours_not_seconds(self):
        monthly = _aggregate_to_monthly(self.fixture, self.start, self.end)
        for m in monthly:
            if m.get("avg_sunshine_hours") is not None:
                # Should be hours (< 24), not seconds (>3600)
                self.assertLess(m["avg_sunshine_hours"], 24.0)
                self.assertGreater(m["avg_sunshine_hours"], 0.0)

    def test_avg_rain_days_reasonable_range(self):
        monthly = _aggregate_to_monthly(self.fixture, self.start, self.end)
        for m in monthly:
            if m.get("avg_rain_days") is not None:
                self.assertGreaterEqual(m["avg_rain_days"], 0)
                self.assertLessEqual(m["avg_rain_days"], 31)


class TestBestMonths(unittest.TestCase):
    def test_best_months_exclude_peak_season(self):
        monthly = [
            {"month": m, "month_name": f"Month{m}", "avg_temp_c": t, "avg_rain_days": r, "peak_season": t > 24 and r < 5}
            for m, t, r in [
                (1, 8.5, 9), (2, 9.0, 8), (3, 11.5, 7), (4, 15.0, 5),
                (5, 19.5, 4), (6, 24.0, 3), (7, 27.5, 1), (8, 27.0, 1),
                (9, 23.5, 4), (10, 18.0, 6), (11, 13.5, 8), (12, 9.5, 9),
            ]
        ]
        best = _derive_best_months(monthly)
        # June and September should be best (warm but not peak season)
        self.assertIn("June", best)
        # July and August are peak season — should not be in best
        self.assertNotIn("July", best)
        self.assertNotIn("August", best)

    def test_best_months_not_empty_for_typical_adriatic(self):
        monthly = [
            {"month": m, "month_name": f"Month{m}", "avg_temp_c": t, "avg_rain_days": r, "peak_season": t > 24 and r < 5}
            for m, t, r in [
                (1, 8.5, 9), (2, 9.0, 8), (3, 11.5, 7), (4, 15.0, 5),
                (5, 19.5, 4), (6, 24.0, 3), (7, 27.5, 1), (8, 27.0, 1),
                (9, 23.5, 4), (10, 18.0, 6), (11, 13.5, 8), (12, 9.5, 9),
            ]
        ]
        best = _derive_best_months(monthly)
        self.assertGreater(len(best), 0)


class TestSeaTemp(unittest.TestCase):
    def test_sea_temp_null_when_marine_data_none(self):
        monthly = [
            {"month": m, "month_name": f"Month{m}", "sea_temp_c": None}
            for m in range(1, 13)
        ]
        result = _merge_sea_temps(monthly, None)
        for m in result:
            self.assertIsNone(m["sea_temp_c"])

    def test_sea_temp_merged_from_hourly_data(self):
        monthly = [
            {"month": m, "month_name": f"Month{m}", "sea_temp_c": None}
            for m in range(1, 13)
        ]
        sea_data = {
            "hourly": {
                "time": ["2023-07-01T00:00", "2023-07-01T01:00", "2023-07-15T00:00"],
                "sea_surface_temperature": [24.5, 24.7, 25.0],
            }
        }
        result = _merge_sea_temps(monthly, sea_data)
        jul = next(m for m in result if m["month"] == 7)
        self.assertIsNotNone(jul["sea_temp_c"])
        self.assertAlmostEqual(jul["sea_temp_c"], 24.7, places=0)


class TestScraperResultOnFailure(unittest.TestCase):
    def test_returns_failed_result_on_exception(self):
        client = MagicMock()
        client.get.side_effect = Exception("connection timeout")
        result = scrape_weather(44.37, 14.69, client)
        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertIn("connection timeout", result.error)

    def test_sea_temp_null_when_marine_fails(self):
        fixture = json.loads((FIXTURES / "silba_weather.json").read_text())
        client = MagicMock()

        from datetime import date
        call_count = [0]

        def side_effect(url, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return fixture
            raise Exception("marine API unavailable")

        client.get.side_effect = side_effect
        result = scrape_weather(44.37, 14.69, client)
        self.assertTrue(result.success)
        for m in result.data["monthly"]:
            self.assertIsNone(m["sea_temp_c"])


if __name__ == "__main__":
    unittest.main()
