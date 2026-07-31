import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_earnings_results


class CollectEarningsEventResultsTests(unittest.TestCase):
    def test_builds_audited_trend_and_risk_from_official_comparisons(self):
        text = (
            "Revenue was $90.0 billion and increased 18%. "
            "Cloud revenue increased 27%. "
            "Gaming revenue decreased 10%."
        )

        facts = collect_earnings_results.extract_directional_facts(text)
        impact = collect_earnings_results.build_rules_impact_review(
            facts,
            datetime.fromisoformat("2026-07-31T14:00:00+09:00"),
        )

        self.assertIsNotNone(impact)
        self.assertEqual(impact["trend_change"], "mixed")
        self.assertEqual(impact["risk_level"], "medium")
        self.assertEqual(impact["cycle_status_effect"], "none_single_source")

    def test_rejects_schedule_announcement_as_published_result(self):
        text = "AMD will report second quarter 2026 financial results on August 4, 2026."
        event = {"scheduled_date": "2026-08-04"}

        self.assertFalse(
            collect_earnings_results.looks_like_published_result(text, event)
        )

    def test_collects_official_result_as_pending_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calendar_path = root / "event-calendar.json"
            results_path = root / "event-results.json"
            calendar_path.write_text(
                (ROOT / "data" / "event-calendar.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            results = json.loads(
                (ROOT / "data" / "event-results.json").read_text(encoding="utf-8")
            )
            results["results"] = [
                item for item in results["results"]
                if item["event_id"] != "earnings-AMD-2026q2"
            ]
            results_path.write_text(json.dumps(results), encoding="utf-8")
            release_url = (
                "https://ir.amd.com/news-events/press-releases/detail/1300/"
                "amd-reports-second-quarter-2026-financial-results"
            )

            def fetcher(url):
                if url.endswith("/financial-information/quarterly-results"):
                    return (
                        f'<a href="{release_url}">'
                        "AMD Q2 2026 financial results</a>"
                    )
                if url == release_url:
                    return (
                        "<h1>AMD Reports Financial Results</h1>"
                        "<p>August 4, 2026</p>"
                    )
                raise OSError(url)

            with (
                mock.patch.object(
                    collect_earnings_results,
                    "CALENDAR_PATH",
                    calendar_path,
                ),
                mock.patch.object(
                    collect_earnings_results,
                    "RESULTS_PATH",
                    results_path,
                ),
            ):
                checked, added = collect_earnings_results.collect(
                    now=datetime.fromisoformat("2026-08-05T06:10:00+09:00"),
                    fetcher=fetcher,
                )

            updated = json.loads(results_path.read_text(encoding="utf-8"))
            result = next(
                item for item in updated["results"]
                if item["event_id"] == "earnings-AMD-2026q2"
            )
            self.assertEqual((checked, added), (1, 1))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["review_status"], "pending")
            self.assertNotIn("impact_review", result)

    def test_discovers_result_from_sec_exhibit_when_ir_is_blocked(self):
        event = {
            "ticker": "AMZN",
            "name": "Amazon 2026 Q2 실적",
            "scheduled_date": "2026-07-30",
        }
        submissions_url = (
            "https://data.sec.gov/submissions/CIK0001018724.json"
        )
        primary_url = (
            "https://www.sec.gov/Archives/edgar/data/1018724/"
            "000101872426000099/amzn-20260730.htm"
        )
        exhibit_url = (
            "https://www.sec.gov/Archives/edgar/data/1018724/"
            "000101872426000099/amzn-20260730xex991.htm"
        )

        def fetcher(url):
            if url == submissions_url:
                return json.dumps({
                    "filings": {
                        "recent": {
                            "form": ["8-K"],
                            "accessionNumber": ["0001018724-26-000099"],
                            "filingDate": ["2026-07-30"],
                            "primaryDocument": ["amzn-20260730.htm"],
                        }
                    }
                })
            if url == primary_url:
                return '<a href="amzn-20260730xex991.htm">EX-99.1</a>'
            if url == exhibit_url:
                return (
                    "<h1>Amazon Reports Financial Results</h1>"
                    "<p>July 30, 2026</p>"
                    "<p>Revenue was $100.0 billion and increased 12%.</p>"
                )
            raise OSError(url)

        result = collect_earnings_results.discover_sec_result(
            event,
            fetcher=fetcher,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[0], exhibit_url)


if __name__ == "__main__":
    unittest.main()
