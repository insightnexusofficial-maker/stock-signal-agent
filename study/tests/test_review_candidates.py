import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from review_candidates import eligible_candidates, needs_sol_audit  # noqa: E402


class ReviewCandidateTests(unittest.TestCase):
    def test_only_fresh_direct_unreviewed_candidates_are_eligible(self):
        inbox = {"candidates": [{
            "id": "a", "published_at": "2026-07-10", "published_at_quality": "direct",
            "content_excerpt": "official fact", "content_hash": "v2",
        }, {
            "id": "b", "published_at": None, "published_at_quality": "unknown", "content_excerpt": "text",
        }]}
        log = {"items": [{"candidate_id": "a", "content_hash": "v1"}]}
        self.assertEqual([item["id"] for item in eligible_candidates(inbox, log, date(2026, 7, 18))], ["a"])

    def test_high_confidence_routes_to_sol(self):
        needed, reason = needs_sol_audit({"confidence_score": 70, "segment": "memory_nand"}, [])
        self.assertTrue(needed)
        self.assertEqual(reason, "confidence>=70")


if __name__ == "__main__":
    unittest.main()
