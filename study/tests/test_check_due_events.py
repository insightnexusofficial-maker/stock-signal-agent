import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_due_events


class CheckDueEventsTests(unittest.TestCase):
    def test_recent_overdue_earnings_gets_limited_daily_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar_path = Path(directory) / "event-calendar.json"
            calendar_path.write_text(
                (ROOT / "data" / "event-calendar.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with mock.patch.object(
                check_due_events,
                "CALENDAR_PATH",
                calendar_path,
            ):
                event_ids = check_due_events.backfill_event_ids(
                    datetime.fromisoformat("2026-07-31T20:00:00+09:00")
                )

        self.assertIn("earnings-META-2026q2", event_ids)
        self.assertIn("earnings-LRCX-2026june", event_ids)
        self.assertNotIn("earnings-AMZN-2026q2", event_ids)

    def test_backfill_stops_three_days_after_announcement(self):
        event_ids = check_due_events.backfill_event_ids(
            datetime.fromisoformat("2026-08-03T20:00:00+09:00")
        )

        self.assertNotIn("earnings-META-2026q2", event_ids)


if __name__ == "__main__":
    unittest.main()
