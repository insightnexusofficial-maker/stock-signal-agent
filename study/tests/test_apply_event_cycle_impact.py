import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_event_cycle_impact


class ApplyEventCycleImpactTests(unittest.TestCase):
    def test_verified_earnings_becomes_segment_specific_evidence(self):
        result = {
            "event_id": "earnings-MSFT-fy2026q4",
            "status": "complete",
            "review_status": "verified",
            "retrieved_at": "2026-07-31T14:00:00+09:00",
            "facts": [
                {"metric": "azure_revenue_yoy_percent", "label": "Azure revenue", "value": 43, "unit": "percent"},
                {"metric": "xbox_revenue_yoy_percent", "label": "Xbox revenue", "value": -10, "unit": "percent"},
            ],
            "source_urls": ["https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast"],
            "impact_review": {"audit_passed": True},
        }
        event = {
            "id": result["event_id"],
            "kind": "earnings",
            "ticker": "MSFT",
            "name": "Microsoft FY2026 Q4 실적",
            "segments": ["cloud_capex", "ai_services"],
        }

        evidence = apply_event_cycle_impact.result_evidence(result, event)

        self.assertEqual(len(evidence), 4)
        self.assertEqual(
            {item["segment"] for item in evidence},
            {"cloud_capex", "ai_services"},
        )
        azure = next(item for item in evidence if "Azure" in item["fact"])
        self.assertEqual(azure["pillar"], "demand")
        self.assertEqual(azure["direction"], "positive")

    def test_single_company_evidence_does_not_force_cycle_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "CALENDAR_PATH": root / "calendar.json",
                "RESULTS_PATH": root / "results.json",
                "EVIDENCE_PATH": root / "evidence.json",
                "COMPANY_MAP_PATH": root / "companies.json",
                "CYCLE_PATH": root / "cycle.json",
            }
            for name, source in (
                ("CALENDAR_PATH", ROOT / "data/event-calendar.json"),
                ("RESULTS_PATH", ROOT / "data/event-results.json"),
                ("EVIDENCE_PATH", ROOT / "data/evidence.json"),
                ("COMPANY_MAP_PATH", ROOT / "data/company-cycle-map.json"),
                ("CYCLE_PATH", ROOT / "public/data/cycle-latest.json"),
            ):
                paths[name].write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            evidence_document = json.loads(paths["EVIDENCE_PATH"].read_text(encoding="utf-8"))
            evidence_document.pop("event_result_hashes", None)
            paths["EVIDENCE_PATH"].write_text(
                json.dumps(evidence_document), encoding="utf-8"
            )
            patches = [mock.patch.object(apply_event_cycle_impact, name, path) for name, path in paths.items()]
            for patch in patches:
                patch.start()
                self.addCleanup(patch.stop)

            outcome = apply_event_cycle_impact.apply(
                datetime.fromisoformat("2026-07-31T20:30:00+09:00")
            )

            evidence = json.loads(paths["EVIDENCE_PATH"].read_text(encoding="utf-8"))
            added = [item for item in evidence["evidence"] if item.get("origin_event_id") == "earnings-MSFT-fy2026q4"]
            self.assertTrue(added)
            self.assertFalse(outcome["critical"])
            if outcome["cycle_changed"]:
                report = json.loads(paths["CYCLE_PATH"].read_text(encoding="utf-8"))
                cloud = next(item for item in report["segments"] if item["id"] == "cloud_capex")
                self.assertEqual(cloud["status"], "neutral")


if __name__ == "__main__":
    unittest.main()
