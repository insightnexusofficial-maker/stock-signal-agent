import unittest
from datetime import datetime

from scripts import build_macro_policy_signal


class MacroPolicySignalTests(unittest.TestCase):
    def test_builds_verified_rate_hike_watch_sector(self):
        payload = build_macro_policy_signal.build(
            now=datetime.fromisoformat("2026-08-29T00:20:00+09:00")
        )

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["quality_gate"]["status"], "passed")
        self.assertEqual(payload["sector"]["id"], "us_rate_policy")
        self.assertEqual(payload["sector"]["status"], "hike_watch")
        self.assertEqual(payload["sector"]["probability_level"], "elevated")
        self.assertGreaterEqual(payload["sector"]["probability_pct"], 50)
        self.assertEqual(len({item["source_family"] for item in payload["evidence"]}), 2)


if __name__ == "__main__":
    unittest.main()
