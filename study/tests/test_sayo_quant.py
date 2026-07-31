import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sayo_quant import apply_peer_context, apply_post_earnings_adjustment, fundamental_gate, fundamental_rating, normalize_stock, price_reflection_rating, valuation_gate  # noqa: E402


class SayoQuantAlignmentTests(unittest.TestCase):
    def test_post_earnings_consensus_cut_reduces_strength_and_raises_price_burden(self):
        previous = {
            "data_as_of": "20260728",
            "metrics": {"forward_eps_growth": 20},
            "ratings": {"fundamental": 80, "price_reflection": 55},
        }
        refreshed = {
            "data_as_of": "20260731",
            "metrics": {"forward_eps_growth": 10, "eps_revision_1m": -2},
            "ratings": {"fundamental": 76, "price_reflection": 60},
        }

        adjusted = apply_post_earnings_adjustment(
            previous,
            refreshed,
            {"event_id": "earnings-TEST-q2"},
            __import__("datetime").datetime.fromisoformat("2026-07-31T20:00:00+09:00"),
        )

        self.assertEqual(adjusted["ratings"]["fundamental"], 66)
        self.assertEqual(adjusted["ratings"]["price_reflection"], 70)
        self.assertEqual(adjusted["assessment"]["consensus_direction"], "lowered")
    def test_semiconductor_peg_uses_same_strict_limit(self):
        criteria = {"peg_max": 0.8}
        self.assertEqual(valuation_gate({"peg_fwd": 0.79, "sector": "semiconductor"}, criteria, "us")["status"], "pass")
        self.assertEqual(valuation_gate({"peg_fwd": 0.8, "sector": "semiconductor"}, criteria, "us")["status"], "fail")

    def test_kr_forward_per_fallback_rejects_negative_forward_growth(self):
        stock = {
            "peg_fwd": None,
            "per_fwd": 7.0,
            "per_source": "fnguide_multi_year_consensus",
            "forward_eps_cagr": -5.0,
            "sector": "semiconductor",
        }
        gate = valuation_gate(stock, {"peg_max": 0.8, "kr_per_fallback_max": 10}, "kr")
        self.assertEqual(gate["status"], "pending")

    def test_fundamental_support_requires_two_hits(self):
        criteria = {"slope_mom_min": 0}
        self.assertEqual(fundamental_gate({"selection_hits": 2, "trend_slope_mom_pct": 0}, criteria)["status"], "pass")
        self.assertEqual(fundamental_gate({"selection_hits": 1, "trend_slope_mom_pct": 10}, criteria)["status"], "fail")

    def test_growth_fallback_matches_all_four_stock_sayo_checks(self):
        criteria = {
            "peg_max": 1.5, "ps_max": 5, "band_max": 30,
            "fallback_surprise_min": 5, "fallback_target_gap_min": 20,
        }
        stock = {
            "sector": "growth", "eps_fwd": -1, "ps": 4.9, "band_pct": 29.9,
            "earnings_surprise_pct": 5, "target_gap": 20,
        }
        self.assertEqual(valuation_gate(stock, criteria, "us")["status"], "pass")
        stock["target_gap"] = 19.9
        self.assertEqual(valuation_gate(stock, criteria, "us")["status"], "fail")

    def test_step1_mismatch_falls_back_to_pending(self):
        stock = {
            "code": "TEST", "sector": "semiconductor", "peg_fwd": 0.4,
            "selection_hits": 2, "trend_slope_mom_pct": 1, "step1": False,
        }
        normalized = normalize_stock(stock, "us", {"semiconductor": {"peg_max": 0.8, "slope_mom_min": 0}})
        self.assertEqual(normalized["sayo_alignment"], "drift")
        self.assertEqual(normalized["valuation_gate"]["status"], "pending")
        self.assertIsNone(normalized["ratings"]["fundamental"])

    def test_fundamental_requires_profitability_or_cash_and_three_pillars(self):
        score, quality = fundamental_rating({
            "forward_eps_cagr": 36.3,
            "operating_margin": 28,
            "return_on_equity": 24,
            "debt_to_equity": 35,
        })
        self.assertGreater(score, 70)
        self.assertEqual(quality["level"], "medium")
        self.assertEqual(quality["available"], 3)
        unavailable, missing_quality = fundamental_rating({"forward_eps_cagr": 36.3})
        self.assertIsNone(unavailable)
        self.assertEqual(missing_quality["level"], "unavailable")

    def test_price_reflection_is_higher_when_expectations_are_expensive(self):
        cheap, _ = price_reflection_rating(
            {"peg_fwd": 0.3, "per_fwd": 10, "band_pct": 20, "target_gap": 40, "peg_quality": "calculated_long_term"},
            {"peg_max": 0.8},
        )
        expensive, _ = price_reflection_rating(
            {"peg_fwd": 1.8, "per_fwd": 60, "band_pct": 90, "target_gap": 5, "peg_quality": "calculated_long_term"},
            {"peg_max": 0.8},
        )
        self.assertLess(cheap, 50)
        self.assertGreater(expensive, 70)

    def test_provider_peg_reduces_reflection_quality(self):
        _, quality = price_reflection_rating(
            {"peg_fwd": 1.27, "per_fwd": 36.8, "band_pct": 79.6, "target_gap": 8.3, "peg_quality": "provider_reported"},
            {"peg_max": 0.8},
        )
        self.assertEqual(quality["level"], "medium")
        self.assertEqual(quality["note"], "provider_peg_horizon_unavailable")

    def test_target_gap_and_price_band_do_not_change_reflection_rating(self):
        base = {"peg_fwd": 0.8, "per_fwd": 20, "pbr": 3, "peg_quality": "calculated_long_term"}
        low_gap, _ = price_reflection_rating({**base, "target_gap": 0, "band_pct": 1}, {"peg_max": 0.8})
        high_gap, _ = price_reflection_rating({**base, "target_gap": 150, "band_pct": 99}, {"peg_max": 0.8})
        self.assertEqual(low_gap, high_gap)

    def test_peer_context_adjusts_only_same_market_sector_with_three_or_more_stocks(self):
        stocks = [
            {"market": "us", "sector": "semiconductor", "data_as_of": "20260719", "ratings": {"fundamental": score, "price_reflection": score}, "rating_quality": {"fundamental": {}, "price_reflection": {}}}
            for score in (30, 50, 70)
        ]
        adjusted = apply_peer_context(stocks)
        self.assertLess(adjusted[0]["ratings"]["fundamental"], adjusted[2]["ratings"]["fundamental"])
        self.assertEqual(adjusted[1]["rating_quality"]["price_reflection"]["peer_group_size"], 3)


if __name__ == "__main__":
    unittest.main()
