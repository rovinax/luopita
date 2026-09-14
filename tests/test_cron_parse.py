from __future__ import annotations

import unittest
from datetime import datetime

from core.clock import SHANGHAI
from core.cron.parse import parse_when, split_cron_args
from core.cron.schedule import next_cron, next_run_at


class TestParseWhen(unittest.TestCase):
    def test_relative_and_every(self):
        at, rest = parse_when("20m 提醒喝水")
        self.assertEqual(at.kind, "at")
        self.assertEqual(at.schedule, "20m")
        self.assertTrue(at.delete_after_run)
        self.assertEqual(rest, "提醒喝水")

        later, rest2 = parse_when("10分钟后 关窗")
        self.assertEqual(later.kind, "at")
        self.assertEqual(later.schedule, "10m")
        self.assertEqual(rest2, "关窗")

        every, rest3 = parse_when("every 1h 查天气")
        self.assertEqual(every.kind, "every")
        self.assertEqual(every.schedule, "1h")
        self.assertFalse(every.delete_after_run)
        self.assertEqual(rest3, "查天气")

        hourly, rest4 = parse_when("每小时 看一眼队列")
        self.assertEqual(hourly.kind, "every")
        self.assertEqual(hourly.schedule, "1h")
        self.assertEqual(rest4, "看一眼队列")

    def test_cron_and_chinese_daily(self):
        cron, rest = parse_when("0 8 * * * 早安")
        self.assertEqual(cron.kind, "cron")
        self.assertEqual(cron.schedule, "0 8 * * *")
        self.assertEqual(rest, "早安")

        daily, rest2 = parse_when("每天8:00 喝水")
        self.assertEqual(daily.kind, "cron")
        self.assertEqual(daily.schedule, "0 8 * * *")
        self.assertEqual(rest2, "喝水")

        daily2, _ = parse_when("每天早上8点")
        self.assertEqual(daily2.schedule, "0 8 * * *")

        evening, _ = parse_when("每天晚上8点")
        self.assertEqual(evening.schedule, "0 20 * * *")

    def test_weekly_and_tomorrow(self):
        frozen = datetime(2026, 9, 14, 9, 0, tzinfo=SHANGHAI)
        weekly, rest = parse_when("每周一8点 开会", now=frozen)
        self.assertEqual(weekly.kind, "cron")
        self.assertEqual(weekly.schedule, "0 8 * * 1")
        self.assertEqual(rest, "开会")

        tomorrow, rest2 = parse_when("明天8:00 叫我", now=frozen)
        self.assertEqual(tomorrow.kind, "at")
        self.assertIn("2026-09-15", tomorrow.schedule)
        self.assertEqual(rest2, "叫我")

    def test_split_subcommand(self):
        self.assertEqual(split_cron_args(""), ("list", ""))
        self.assertEqual(split_cron_args("list"), ("list", ""))
        self.assertEqual(split_cron_args("add 20m 喝水"), ("add", "20m 喝水"))
        self.assertEqual(split_cron_args("20m 喝水"), ("add", "20m 喝水"))
        self.assertEqual(split_cron_args("rm abcdef12"), ("rm", "abcdef12"))


class TestNextRun(unittest.TestCase):
    def test_every_and_at_duration(self):
        frozen = datetime(2026, 9, 14, 8, 0, tzinfo=SHANGHAI)
        nxt = next_run_at("every", "1h", now=frozen)
        self.assertEqual(nxt.hour, 9)
        at = next_run_at("at", "20m", now=frozen)
        self.assertEqual(at.hour, 8)
        self.assertEqual(at.minute, 20)

    def test_cron_tomorrow_if_past(self):
        frozen = datetime(2026, 9, 14, 9, 1, tzinfo=SHANGHAI)
        nxt = next_cron("0 8 * * *", frozen)
        self.assertEqual(nxt.day, 15)
        self.assertEqual(nxt.hour, 8)
        self.assertEqual(nxt.minute, 0)

    def test_cron_same_day_if_future(self):
        frozen = datetime(2026, 9, 14, 7, 0, tzinfo=SHANGHAI)
        nxt = next_cron("0 8 * * *", frozen)
        self.assertEqual(nxt.day, 14)
        self.assertEqual(nxt.hour, 8)
