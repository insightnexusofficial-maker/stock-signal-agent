from __future__ import annotations

import sys
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_event_results


class CollectEventResultsTests(unittest.TestCase):
    def test_discovers_latest_official_html_statement(self):
        html = """
        <a href="/newsevents/pressreleases/monetary20260617a.htm">HTML</a>
        <a href="/newsevents/pressreleases/monetary20260729a.htm">HTML</a>
        <a href="/newsevents/pressreleases/monetary20260729a.pdf">PDF</a>
        """
        statement_date, url = collect_event_results.discover_latest_fomc_statement(html)
        self.assertEqual(statement_date, "2026-07-29")
        self.assertEqual(
            url,
            "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
        )

    def test_parses_fractional_target_range(self):
        html = """
        <p>The Committee decided to maintain the target range for the federal
        funds rate at 3-1/2 to 3-3/4 percent.</p>
        """
        self.assertEqual(
            collect_event_results.parse_fomc_target_range(html),
            (3.5, 3.75),
        )

    def test_builds_verified_non_shock_result(self):
        previous = {
            "results": [{
                "event_id": "macro-fomc-2026-06-17",
                "source_published_at": "2026-06-18T03:00:00+09:00",
                "facts": [
                    {"metric": "federal_funds_target_range_lower", "value": 3.5},
                    {"metric": "federal_funds_target_range_upper", "value": 3.75},
                ],
            }]
        }
        result = collect_event_results.build_fomc_result(
            "2026-07-29",
            "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
            3.5,
            3.75,
            previous,
            datetime.fromisoformat("2026-07-30T03:10:00+09:00"),
        )
        self.assertEqual(result["review_status"], "verified")
        self.assertNotIn("shock", result)
        change = next(
            fact for fact in result["facts"]
            if fact["metric"] == "federal_funds_target_range_change"
        )
        self.assertEqual(change["value"], 0)

    def test_marks_50bp_change_as_audited_shock(self):
        previous = {
            "results": [{
                "event_id": "macro-fomc-2026-06-17",
                "source_published_at": "2026-06-18T03:00:00+09:00",
                "facts": [
                    {"metric": "federal_funds_target_range_lower", "value": 4.0},
                    {"metric": "federal_funds_target_range_upper", "value": 4.25},
                ],
            }]
        }
        result = collect_event_results.build_fomc_result(
            "2026-07-29",
            "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
            3.5,
            3.75,
            previous,
            datetime.fromisoformat("2026-07-30T03:10:00+09:00"),
        )
        self.assertTrue(result["shock"]["is_shock"])
        self.assertTrue(result["shock"]["audit_passed"])

    def test_collect_updates_direct_statement_url_without_rewriting_result(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            calendar_path = temporary / "event-calendar.json"
            results_path = temporary / "event-results.json"
            calendar_path.write_text(
                (ROOT / "data" / "event-calendar.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            results_document = json.loads(
                (ROOT / "data" / "event-results.json").read_text(encoding="utf-8")
            )
            results_document["results"] = [
                item for item in results_document["results"]
                if item["event_id"] != "macro-fomc-2026-07-29"
            ]
            results_path.write_text(json.dumps(results_document), encoding="utf-8")
            calendar_html = """
            <a href="/newsevents/pressreleases/monetary20260729a.htm">HTML</a>
            """
            statement_html = """
            <p>The Committee decided to maintain the target range for the federal
            funds rate at 3-1/2 to 3-3/4 percent.</p>
            """
            with (
                mock.patch.object(collect_event_results, "CALENDAR_PATH", calendar_path),
                mock.patch.object(collect_event_results, "RESULTS_PATH", results_path),
                mock.patch.object(
                    collect_event_results,
                    "_fetch_official_html",
                    side_effect=[calendar_html, statement_html],
                ),
            ):
                changed = collect_event_results.collect(
                    datetime.fromisoformat("2026-07-30T13:30:00+09:00")
                )
            self.assertTrue(changed)
            calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
            event = next(
                item for item in calendar["events"]
                if item["id"] == "macro-fomc-2026-07-29"
            )
            self.assertEqual(
                event["result_source_url"],
                "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
            )
            results = json.loads(results_path.read_text(encoding="utf-8"))
            fomc = next(
                item for item in results["results"]
                if item["event_id"] == "macro-fomc-2026-07-29"
            )
            self.assertEqual(fomc["retrieved_at"], "2026-07-30T13:30:00+09:00")


if __name__ == "__main__":
    unittest.main()
