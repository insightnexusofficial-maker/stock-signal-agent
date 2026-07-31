import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import discover_earnings_calendar


class DiscoverEarningsCalendarTests(unittest.TestCase):
    def test_finds_future_date_only_in_earnings_context(self):
        html = """
        <html><body>
          <p>Investor conference August 1, 2026</p>
          <h2>Q2 2026 Earnings Conference Call</h2>
          <p>August 13, 2026 at 4:30 PM EDT</p>
        </body></html>
        """

        found = discover_earnings_calendar.discover_official_earnings_date(
            html,
            date(2026, 7, 31),
        )

        self.assertEqual(found, date(2026, 8, 13))

    def test_ignores_past_and_non_earnings_dates(self):
        html = """
        <html><body>
          <p>Annual meeting August 2, 2026</p>
          <p>Q1 earnings May 4, 2026</p>
        </body></html>
        """

        found = discover_earnings_calendar.discover_official_earnings_date(
            html,
            date(2026, 7, 31),
        )

        self.assertIsNone(found)

    def test_adds_date_confirmed_event_without_guessing_time(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar_path = Path(directory) / "event-calendar.json"
            calendar = json.loads(
                (ROOT / "data" / "event-calendar.json").read_text(encoding="utf-8")
            )
            calendar["events"] = [
                item for item in calendar["events"]
                if item.get("ticker") != "PLTR"
            ]
            calendar_path.write_text(json.dumps(calendar), encoding="utf-8")

            def fetcher(url):
                if "palantir" in url:
                    return "<h2>Q2 2026 Earnings</h2><p>August 4, 2026</p>"
                raise OSError("fixture unavailable")

            with mock.patch.object(
                discover_earnings_calendar,
                "CALENDAR_PATH",
                calendar_path,
            ):
                _, _, added = discover_earnings_calendar.discover(
                    now=datetime.fromisoformat("2026-07-31T12:00:00+09:00"),
                    fetcher=fetcher,
                )

            updated = json.loads(calendar_path.read_text(encoding="utf-8"))
            event = next(
                item for item in updated["events"]
                if item["id"] == "earnings-PLTR-2026-08-04"
            )
            self.assertEqual(added, 1)
            self.assertEqual(event["schedule_status"], "date_confirmed")
            self.assertIsNone(event["scheduled_at"])


if __name__ == "__main__":
    unittest.main()
