import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cycle_engine import build_report, project_company_cycles  # noqa: E402


class CycleEngineTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 18, 7, 0, tzinfo=timezone(timedelta(hours=9)))

    def evidence(self, item_id, family, pillar, direction="positive", audit=True):
        return {
            "id": item_id,
            "segment": "memory_hbm_dram",
            "pillar": pillar,
            "direction": direction,
            "fact": "검증된 사실",
            "source_url": f"https://example.com/{item_id}",
            "published_at": "2026-07-10",
            "source_family": family,
            "confidence": "direct",
            "review_status": "verified",
            "audit_passed": audit,
        }

    def segment(self, report):
        return next(item for item in report["segments"] if item["id"] == "memory_hbm_dram")

    def test_two_families_and_two_pillars_can_be_favorable(self):
        evidence = [
            self.evidence("a", "manufacturer_ir", "pricing"),
            self.evidence("b", "industry_body", "demand"),
        ]
        self.assertEqual(self.segment(build_report(evidence, self.now))["status"], "favorable")
        self.assertEqual(build_report(evidence, self.now)["schema_version"], "1.2")

    def test_one_family_defaults_to_neutral(self):
        evidence = [
            self.evidence("a", "manufacturer_ir", "pricing"),
            self.evidence("b", "manufacturer_ir", "demand"),
        ]
        self.assertEqual(self.segment(build_report(evidence, self.now))["status"], "neutral")

    def test_missing_audit_defaults_to_neutral(self):
        evidence = [
            self.evidence("a", "manufacturer_ir", "pricing", audit=False),
            self.evidence("b", "industry_body", "demand"),
        ]
        self.assertEqual(self.segment(build_report(evidence, self.now))["status"], "neutral")

    def test_old_evidence_is_excluded(self):
        item = self.evidence("a", "manufacturer_ir", "pricing")
        item["published_at"] = "2026-05-01"
        self.assertEqual(self.segment(build_report([item], self.now))["supporting_evidence_ids"], [])

    def test_neutral_keeps_positive_and_negative_ids_separate(self):
        evidence = [
            self.evidence("positive", "manufacturer_ir", "pricing", "positive"),
            self.evidence("negative", "industry_body", "inventory", "negative"),
        ]
        segment = self.segment(build_report(evidence, self.now))
        self.assertEqual(segment["status"], "neutral")
        self.assertEqual(segment["supporting_evidence_ids"], ["positive"])
        self.assertEqual(segment["contrary_evidence_ids"], ["negative"])

    def test_empty_evidence_publishes_quality_gate_neutral(self):
        report = build_report([], self.now)
        self.assertEqual(report["fallback_status"], "pending-neutral")
        self.assertEqual(report["quality_gate"]["evidence_count"], 0)
        self.assertEqual(report["quality_gate"]["status"], "insufficient")
        self.assertTrue(all(item["status"] == "neutral" for item in report["segments"]))
        self.assertRegex(report["evidence_sha256"], r"^[0-9a-f]{64}$")

    def test_quarterly_retention_does_not_override_thirty_day_status_limit(self):
        evidence = self.evidence("old", "company_ir", "earnings", "positive")
        evidence["published_at"] = "2026-06-17"
        evidence["max_age_days"] = 120
        report = build_report([evidence], self.now)
        self.assertEqual(report["quality_gate"]["status"], "insufficient")
        self.assertEqual(report["quality_gate"]["evidence_count"], 0)

    def test_company_projection_uses_primary_segment(self):
        segments = [
            {"id": "memory_hbm_dram", "label": "HBM·DRAM", "status": "favorable", "confidence": 80},
            {"id": "foundry_logic", "label": "파운드리·로직", "status": "neutral", "confidence": 35},
        ]
        companies = [{
            "ticker": "TEST", "name": "테스트",
            "exposures": [
                {"segment": "memory_hbm_dram", "weight": 0.8},
                {"segment": "foundry_logic", "weight": 0.2},
            ],
        }]

        result = project_company_cycles(companies, segments)

        self.assertEqual(result[0]["status"], "favorable")
        self.assertEqual(result[0]["primary_segment"], "memory_hbm_dram")

    def test_company_projection_neutralizes_opposing_segments(self):
        segments = [
            {"id": "memory_hbm_dram", "label": "HBM·DRAM", "status": "favorable", "confidence": 80},
            {"id": "foundry_logic", "label": "파운드리·로직", "status": "caution", "confidence": 75},
        ]
        companies = [{
            "ticker": "TEST", "name": "테스트",
            "exposures": [
                {"segment": "memory_hbm_dram", "weight": 0.7},
                {"segment": "foundry_logic", "weight": 0.3},
            ],
        }]

        self.assertEqual(project_company_cycles(companies, segments)[0]["status"], "neutral")


if __name__ == "__main__":
    unittest.main()
