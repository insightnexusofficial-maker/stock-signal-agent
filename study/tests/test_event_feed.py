import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import event_feed


class EventFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar = json.loads(
            event_feed.DEFAULT_CALENDAR_PATH.read_text(encoding="utf-8")
        )
        cls.results = json.loads(
            event_feed.DEFAULT_RESULTS_PATH.read_text(encoding="utf-8")
        )

    def write_documents(self, calendar=None, results=None):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        calendar_path = root / "calendar.json"
        results_path = root / "results.json"
        calendar_path.write_text(
            json.dumps(calendar or self.calendar),
            encoding="utf-8",
        )
        results_path.write_text(
            json.dumps(results or self.results),
            encoding="utf-8",
        )
        return temp, calendar_path, results_path

    def test_checked_in_documents_pass_contract(self):
        calendar = event_feed.validate_calendar(deepcopy(self.calendar))
        results = event_feed.validate_results(deepcopy(self.results), calendar)
        self.assertGreaterEqual(len(calendar["monitors"]), 18)
        self.assertIsInstance(results["results"], list)

    def test_due_event_is_not_reported_as_synced_without_verified_result(self):
        temp, calendar_path, results_path = self.write_documents()
        self.addCleanup(temp.cleanup)

        sync = event_feed.build_event_sync(
            calendar_path,
            results_path,
            now=datetime.fromisoformat("2026-07-30T07:00:00+09:00"),
        )

        self.assertIn("earnings-MSFT-fy2026q4", sync["due_event_ids"])
        self.assertIn(
            "earnings-MSFT-fy2026q4",
            sync["unsupported_due_event_ids"],
        )
        event = next(
            item for item in sync["upcoming"]
            if item["id"] == "earnings-MSFT-fy2026q4"
        )
        self.assertEqual(event["sync_status"], "due")
        self.assertEqual(event["result_collection_status"], "manual-official-review")

    def test_fomc_due_event_has_an_automated_official_collector(self):
        results = deepcopy(self.results)
        results["results"] = [
            item for item in results["results"]
            if item["event_id"] != "macro-fomc-2026-07-29"
        ]
        temp, calendar_path, results_path = self.write_documents(results=results)
        self.addCleanup(temp.cleanup)

        sync = event_feed.build_event_sync(
            calendar_path,
            results_path,
            now=datetime.fromisoformat("2026-07-30T03:10:00+09:00"),
        )

        event = next(
            item for item in sync["upcoming"]
            if item["id"] == "macro-fomc-2026-07-29"
        )
        self.assertEqual(event["sync_status"], "due")
        self.assertEqual(event["result_collection_status"], "automated-official")
        self.assertNotIn(
            "macro-fomc-2026-07-29",
            sync["unsupported_due_event_ids"],
        )

    def test_date_only_event_never_becomes_due_at_a_guessed_time(self):
        calendar = deepcopy(self.calendar)
        event = next(
            item for item in calendar["events"]
            if item["id"] == "earnings-NVDA-fy2027q2"
        )
        event.pop("scheduled_at")
        event["schedule_status"] = "date_confirmed"
        event["monitor_after"] = "2026-08-27T05:00:00+09:00"
        temp, calendar_path, results_path = self.write_documents()
        calendar_path.write_text(json.dumps(calendar), encoding="utf-8")
        self.addCleanup(temp.cleanup)

        sync = event_feed.build_event_sync(
            calendar_path,
            results_path,
            now=datetime.fromisoformat("2026-08-27T08:00:00+09:00"),
        )

        self.assertNotIn("earnings-NVDA-fy2027q2", sync["due_event_ids"])
        event = next(
            item for item in sync["upcoming"]
            if item["id"] == "earnings-NVDA-fy2027q2"
        )
        self.assertEqual(event["sync_status"], "scheduled")
        self.assertIsNone(event["scheduled_at"])

    def test_confirmed_event_requires_exact_timezone_aware_time(self):
        calendar = deepcopy(self.calendar)
        event = next(
            item for item in calendar["events"]
            if item["id"] == "earnings-NVDA-fy2027q2"
        )
        event["schedule_status"] = "confirmed"
        event.pop("scheduled_at")

        with self.assertRaises(event_feed.EventDataError):
            event_feed.validate_calendar(calendar)

    def test_only_verified_complete_result_marks_event_synced(self):
        results = deepcopy(self.results)
        results["generated_at"] = "2026-07-30T07:10:00+09:00"
        results["results"] = [{
            "event_id": "earnings-MSFT-fy2026q4",
            "status": "complete",
            "review_status": "verified",
            "retrieved_at": "2026-07-30T07:10:00+09:00",
            "source_published_at": "2026-07-29T20:10:00+00:00",
            "reference_period": "FY2026 Q4",
            "summary": "공식 실적 발표 확인",
            "facts": [{"metric": "revenue", "value": 1, "unit": "USD"}],
            "source_urls": [
                "https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast"
            ],
        }]
        temp, calendar_path, results_path = self.write_documents(results=results)
        self.addCleanup(temp.cleanup)

        sync = event_feed.build_event_sync(
            calendar_path,
            results_path,
            now=datetime.fromisoformat("2026-07-30T07:20:00+09:00"),
        )

        event = next(
            item for item in sync["upcoming"]
            if item["id"] == "earnings-MSFT-fy2026q4"
        )
        self.assertEqual(event["sync_status"], "synced")
        self.assertEqual(sync["recent_results"][0]["event_id"], event["id"])
        self.assertEqual(
            sync["recent_results"][0]["source_published_at"],
            "2026-07-30T05:10:00+09:00",
        )

    def test_complete_result_requires_verified_review(self):
        results = deepcopy(self.results)
        results["results"] = [{
            "event_id": "macro-us-cpi-2026-07",
            "status": "complete",
            "review_status": "pending",
            "retrieved_at": "2026-08-12T21:40:00+09:00",
            "source_urls": ["https://www.bls.gov/cpi/"],
        }]

        with self.assertRaises(event_feed.EventDataError):
            event_feed.validate_results(results, self.calendar)

    def test_unapproved_result_domain_is_rejected(self):
        results = deepcopy(self.results)
        results["results"] = [{
            "event_id": "macro-us-cpi-2026-07",
            "status": "partial",
            "review_status": "pending",
            "retrieved_at": "2026-08-12T21:40:00+09:00",
            "source_urls": ["https://example.com/cpi"],
        }]

        with self.assertRaises(event_feed.EventDataError):
            event_feed.validate_results(results, self.calendar)

    def test_expired_calendar_is_explicitly_stale(self):
        temp, calendar_path, results_path = self.write_documents()
        self.addCleanup(temp.cleanup)

        sync = event_feed.build_event_sync(
            calendar_path,
            results_path,
            now=datetime.fromisoformat("2026-08-06T09:00:00+09:00"),
        )

        self.assertEqual(sync["calendar_status"], "stale")

    def test_public_feed_has_hash_and_is_display_only(self):
        temp, calendar_path, results_path = self.write_documents()
        self.addCleanup(temp.cleanup)

        feed = event_feed.build_public_feed(
            calendar_path,
            results_path,
            now=datetime.fromisoformat("2026-07-29T08:00:00+09:00"),
        )

        self.assertRegex(feed["content_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(feed["feed_id"].startswith("market-events-"))
        self.assertEqual(feed["quality_gate"]["mode"], "official-only")
        self.assertNotIn("buy_level", json.dumps(feed))
        self.assertIn("unsupported_due_event_ids", feed["event_sync"])

    def test_verified_shock_gets_next_morning_seven_notification(self):
        results = deepcopy(self.results)
        results["generated_at"] = "2026-08-12T21:40:00+09:00"
        results["results"] = [{
            "event_id": "macro-us-cpi-2026-07",
            "status": "complete",
            "review_status": "verified",
            "retrieved_at": "2026-08-12T21:40:00+09:00",
            "source_published_at": "2026-08-12T21:30:00+09:00",
            "reference_period": "2026-07",
            "summary": "공식 CPI 발표 확인",
            "facts": [{"metric": "cpi_mom", "value": 0.7, "unit": "percent"}],
            "source_urls": ["https://www.bls.gov/cpi/"],
            "shock": {
                "is_shock": True,
                "severity": "shock",
                "rule_id": "macro-inflation-mom-0_6pct",
                "reason": "공식 CPI 전월 대비 변동률 절대값이 0.6% 이상",
                "audit_passed": True
            }
        }]
        temp, calendar_path, results_path = self.write_documents(results=results)
        self.addCleanup(temp.cleanup)

        feed = event_feed.build_public_feed(
            calendar_path,
            results_path,
            now=datetime.fromisoformat("2026-08-12T22:00:00+09:00"),
        )

        shock = feed["recent_results"][0]["shock"]
        self.assertEqual(shock["notify_at"], "2026-08-13T07:00:00+09:00")
        self.assertTrue(shock["audit_passed"])

    def test_shock_without_audit_is_rejected(self):
        results = deepcopy(self.results)
        results["results"] = [{
            "event_id": "macro-us-cpi-2026-07",
            "status": "complete",
            "review_status": "verified",
            "retrieved_at": "2026-08-12T21:40:00+09:00",
            "source_urls": ["https://www.bls.gov/cpi/"],
            "shock": {
                "is_shock": True,
                "severity": "shock",
                "rule_id": "macro-inflation-mom-0_6pct",
                "reason": "임계값 초과",
                "audit_passed": False
            }
        }]

        with self.assertRaises(event_feed.EventDataError):
            event_feed.validate_results(results, self.calendar)


if __name__ == "__main__":
    unittest.main()
