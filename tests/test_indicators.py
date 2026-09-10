"""Numerical checks independent of providers and the user's database."""
import math
import unittest

import pandas as pd

from backend.analyzer import technical_indicators
from backend.indicators import chart_payload, kdj_reading, macd_reading, oscillator_frame


def prices(count=160):
    close = [100 + i * .07 + math.sin(i / 4) * 8 for i in range(count)]
    return pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=count),
                         "close": close, "high": [v + 2 for v in close], "low": [v - 3 for v in close]})


class IndicatorTests(unittest.TestCase):
    def test_series_matches_independent_recursive_calculation(self):
        frame = prices(80)
        actual = oscillator_frame(frame)
        fast = slow = frame.iloc[0]["close"]
        dea = 0
        k = d = 50
        for i, row in frame.iterrows():
            fast = fast * (11 / 13) + row["close"] * (2 / 13)
            slow = slow * (25 / 27) + row["close"] * (2 / 27)
            dif = fast - slow
            dea = dea * .8 + dif * .2
            window = frame.iloc[max(0, i - 8):i + 1]
            rsv = 50 if i < 8 else (row["close"] - window["low"].min()) / (window["high"].max() - window["low"].min()) * 100
            k = k * (2 / 3) + rsv / 3
            d = d * (2 / 3) + k / 3
            for key, value in {"dif": dif, "dea": dea, "macd_bar": 2 * (dif - dea), "k": k, "d": d, "j": 3 * k - 2 * d}.items():
                self.assertAlmostEqual(actual.iloc[i][key], value, places=9)

    def test_display_tail_retains_full_history_and_matches_summary_values(self):
        frame = prices()
        payload = chart_payload(frame)
        self.assertEqual(len(payload["series"]), 120)
        self.assertEqual(payload["series"][0]["date"], frame.iloc[-120]["date"].strftime("%Y-%m-%d"))
        self.assertEqual(payload["series"][-1]["date"], frame.iloc[-1]["date"].strftime("%Y-%m-%d"))
        summary = technical_indicators(frame)
        for key in ("dif", "dea", "macd_bar"):
            self.assertAlmostEqual(payload["series"][-1][key], summary["macd"][key], delta=.00051)
        for key in ("k", "d", "j"):
            self.assertAlmostEqual(payload["series"][-1][key], summary["kdj"][key], delta=.051)
        # Reinitializing at the visible 30-day range would change the last reading.
        self.assertNotAlmostEqual(payload["series"][-1]["dif"], oscillator_frame(frame.tail(30)).iloc[-1]["dif"], places=3)

    def test_flat_price_is_finite_and_neutral(self):
        frame = prices(40)
        frame[["close", "high", "low"]] = 100
        series = oscillator_frame(frame)
        self.assertTrue((series[["dif", "dea", "macd_bar"]] == 0).all().all())
        self.assertTrue((series[["k", "d", "j"]] == 50).all().all())
        payload = chart_payload(frame)
        self.assertIsNone(payload["macd"]["cross"])
        self.assertIsNone(payload["kdj"]["cross"])

    def test_initialization_is_not_drawn_as_observed_history(self):
        payload = chart_payload(prices(35))
        self.assertTrue(all(row["dif"] is None for row in payload["series"][:34]))
        self.assertIsNotNone(payload["series"][-1]["dif"])
        self.assertTrue(all(row["k"] is None for row in payload["series"][:8]))
        self.assertIsNotNone(payload["series"][8]["k"])
        self.assertIsNone(chart_payload(prices(20))["macd"])
        self.assertIsNone(technical_indicators(prices(20))["macd"]["dif"])

    def test_negative_histogram_shrinks_above_zero_without_false_cross(self):
        reading = macd_reading({"dif": 1, "dea": 2, "macd_bar": -2}, {"dif": 1, "dea": 3, "macd_bar": -4})
        self.assertIn("零轴上方", reading["summary"])
        self.assertIn("负柱缩短", reading["summary"])
        self.assertIn("下行动能减弱", reading["summary"])
        self.assertIsNone(reading["cross"])

    def test_actual_cross_is_distinguished_from_existing_order(self):
        previous = {"dif": -2, "dea": -1, "macd_bar": -2}
        current = {"dif": -.5, "dea": -1, "macd_bar": 1}
        self.assertEqual(macd_reading(current, previous)["cross"], "golden")
        self.assertEqual(macd_reading(previous, current)["cross"], "death")
        self.assertIsNone(kdj_reading({"k": 79, "d": 73, "j": 91}, {"k": 75, "d": 71, "j": 83})["cross"])
        self.assertEqual(kdj_reading({"k": 71, "d": 70, "j": 73}, {"k": 69, "d": 70, "j": 67})["cross"], "golden")
        from_zero = macd_reading(current, {"dif": -1, "dea": -1, "macd_bar": 0})
        self.assertIn("由零转正", from_zero["summary"])

    def test_kdj_extremes_are_not_clamped(self):
        high = kdj_reading({"k": 99, "d": 85, "j": 127}, {"k": 97, "d": 83, "j": 125})
        low = kdj_reading({"k": 1, "d": 15, "j": -27}, {"k": 3, "d": 17, "j": -25})
        self.assertIn("J 超过 100", high["detail"])
        self.assertIn("J 低于 0", low["detail"])


if __name__ == '__main__':
    unittest.main()
