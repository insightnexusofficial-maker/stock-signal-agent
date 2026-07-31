import unittest
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_sayo_quant import merge_earnings_lifecycle


class SyncSayoQuantLifecycleTests(unittest.TestCase):
    def stock(self, ticker, date_value, fundamental):
        return {
            "ticker": ticker,
            "data_as_of": date_value,
            "metrics": {"forward_eps_growth": 20, "eps_revision_1m": 0},
            "ratings": {"fundamental": fundamental, "price_reflection": 50},
        }

    def test_unannounced_company_keeps_previous_assessment(self):
        previous = {
            "generated_at": "2026-07-28T07:00:00+09:00",
            "stocks": [self.stock("AMD", "20260728", 70)],
        }
        refreshed = {
            "generated_at": "2026-07-31T20:00:00+09:00",
            "stocks": [self.stock("AMD", "20260731", 90)],
        }

        merged = merge_earnings_lifecycle(
            previous,
            refreshed,
            {"events": []},
            {"results": []},
            datetime.fromisoformat("2026-07-31T20:00:00+09:00"),
        )

        self.assertEqual(merged["stocks"][0]["ratings"]["fundamental"], 70)
        self.assertEqual(merged["stocks"][0]["assessment"]["state"], "held_until_next_earnings")

    def test_verified_earnings_allows_newer_eps_assessment(self):
        previous = {
            "generated_at": "2026-07-28T07:00:00+09:00",
            "stocks": [self.stock("MSFT", "20260728", 70)],
        }
        refreshed_stock = self.stock("MSFT", "20260731", 82)
        refreshed_stock["metrics"]["eps_revision_1m"] = 2
        refreshed = {
            "generated_at": "2026-07-31T20:00:00+09:00",
            "stocks": [refreshed_stock],
        }
        calendar = {"events": [{"id": "earnings-MSFT-q4", "kind": "earnings", "ticker": "MSFT"}]}
        results = {"results": [{
            "event_id": "earnings-MSFT-q4",
            "status": "complete",
            "review_status": "verified",
            "retrieved_at": "2026-07-31T14:00:00+09:00",
        }]}

        merged = merge_earnings_lifecycle(
            previous,
            refreshed,
            calendar,
            results,
            datetime.fromisoformat("2026-07-31T20:00:00+09:00"),
        )

        self.assertEqual(merged["stocks"][0]["ratings"]["fundamental"], 82)
        self.assertEqual(merged["stocks"][0]["assessment"]["state"], "updated_after_earnings")


if __name__ == "__main__":
    unittest.main()
