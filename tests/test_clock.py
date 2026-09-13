from __future__ import annotations

from datetime import datetime, timezone
import unittest

from core.clock import SHANGHAI, format_hhmm, format_now, now_shanghai


class TestClock(unittest.TestCase):
    def test_format_now_has_date_weekday_time_and_zone(self):
        stamp = datetime(2026, 9, 12, 14, 41, tzinfo=SHANGHAI)
        text = format_now(stamp)
        self.assertEqual(text, "现在是北京时间 2026-09-12 星期六 14:41（UTC+8）")

    def test_naive_datetime_treated_as_shanghai(self):
        stamp = datetime(2026, 9, 12, 14, 41)
        self.assertEqual(now_shanghai(stamp).tzinfo, SHANGHAI)
        self.assertIn("14:41", format_now(stamp))

    def test_utc_iso_converts_to_shanghai_hhmm(self):
        self.assertEqual(format_hhmm("2026-09-12T06:41:00+00:00"), "14:41")
        self.assertEqual(format_hhmm("2026-09-12T06:41:00Z"), "14:41")
        utc = datetime(2026, 9, 12, 6, 41, tzinfo=timezone.utc)
        self.assertEqual(format_hhmm(utc), "14:41")
        self.assertEqual(format_hhmm(""), "")
        self.assertEqual(format_hhmm(None), "")


if __name__ == "__main__":
    unittest.main()
