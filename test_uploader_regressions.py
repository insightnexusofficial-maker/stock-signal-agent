import unittest
import json
from unittest.mock import Mock, patch

import pandas as pd

import uploader


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class UploaderRegressionTests(unittest.TestCase):
    @patch("uploader.get_fnguide_forward_consensus")
    @patch("uploader.get_naver_consensus")
    def test_kr_forward_peg_uses_multi_year_forward_consensus(self, naver, fnguide):
        naver.return_value = {
            "per_fwd": 12.0,
            "eps_fwd": 1200.0,
            "eps_ttm": 1000.0,
            "eps_growth": 20.0,
            "annual_eps_growth": 20.0,
            "eps_growth_source": "naver_annual_consensus_yoy",
        }
        fnguide.return_value = {
            "per_fwd": 5.46,
            "per_source": "fnguide_multi_year_consensus",
            "eps_fwd": 46663.99,
            "forward_eps_cagr": 18.8,
            "forward_eps_cagr_years": 2,
        }

        result = uploader.get_kr_valuation("005930")

        self.assertEqual(result["peg_raw"], 0.29)
        self.assertEqual(result["peg_fwd"], 0.29)
        self.assertEqual(result["peg_quality"], "multi_year_forward_consensus")
        self.assertEqual(result["peg_growth_horizon"], "2y_forward_consensus")

    def test_fnguide_forward_consensus_parser_uses_estimate_columns_only(self):
        trend = {
            "header": [
                {"YYMM": "2025/12", "EP_CHK": " ", "CD": "VAL1"},
                {"YYMM": "2026/12", "EP_CHK": "E", "CD": "VAL2"},
                {"YYMM": "2027/12", "EP_CHK": "E", "CD": "VAL3"},
                {"YYMM": "2028/12", "EP_CHK": "E", "CD": "VAL4"},
            ],
            "data": [
                {"NAME": "EPS", "VAL1": "100", "VAL2": "200", "VAL3": "242", "VAL4": "288"},
                {"NAME": "PER", "VAL1": "20", "VAL2": "10", "VAL3": "8", "VAL4": "7"},
            ],
        }
        html = f"perforTrend: {json.dumps(trend)}, perforTrendChart: {{}}"

        result = uploader._extract_fnguide_forward_consensus(html)

        self.assertEqual(result["forward_eps_cagr"], 20.0)
        self.assertEqual(result["forward_eps_cagr_years"], 2)
        self.assertEqual(result["per_fwd"], 10.0)
        self.assertEqual([item["period"] for item in result["forward_eps_estimates"]], [
            "2026/12", "2027/12", "2028/12",
        ])

    def test_kr_forward_peg_hides_non_positive_forward_growth(self):
        data = {"per_fwd": 7.56, "forward_eps_cagr": -5.3, "forward_eps_cagr_years": 2}

        peg = uploader.apply_kr_peg(data)

        self.assertIsNone(peg)
        self.assertIsNone(data["peg_fwd"])
        self.assertEqual(data["peg_quality"], "unavailable_non_positive_forward_growth")

    @patch("uploader.yf.Ticker")
    def test_us_forward_peg_prefers_long_term_analyst_growth(self, ticker_factory):
        ticker = Mock()
        ticker.info = {
            "currentPrice": 100.0,
            "forwardPE": 20.0,
            "forwardEps": 5.0,
            "trailingEps": 4.0,
        }
        ticker.growth_estimates = pd.DataFrame(
            {"stockTrend": [0.2, 0.4], "indexTrend": [0.1, 0.1]},
            index=["+5y", "+1y"],
        )
        ticker.earnings_history = pd.DataFrame()
        ticker.history.return_value = pd.DataFrame()
        ticker_factory.return_value = ticker

        result = uploader.get_us_stock_data("TEST")

        self.assertEqual(result["eps_growth"], 20.0)
        self.assertEqual(result["peg_fwd"], 1.0)
        self.assertEqual(result["peg_source"], "yahoo_growth_estimates_5y_calculated")
        self.assertEqual(result["peg_growth_horizon"], "long_term_forward")

    @patch("uploader.yf.Ticker")
    def test_us_provider_peg_wins_and_growth_over_100_pct_keeps_unit(self, ticker_factory):
        ticker = Mock()
        ticker.info = {
            "currentPrice": 853.2,
            "forwardPE": 5.6704,
            "trailingPE": 19.2683,
            "forwardEps": 150.465,
            "trailingEps": 44.28,
            "pegRatio": 0.13,
        }
        ticker.growth_estimates = pd.DataFrame(
            {"stockTrend": [1.0509]}, index=["+1y"]
        )
        ticker.earnings_history = pd.DataFrame()
        ticker.history.return_value = pd.DataFrame()
        ticker_factory.return_value = ticker

        result = uploader.get_us_stock_data("MU")

        self.assertEqual(result["eps_growth"], 105.1)
        self.assertEqual(result["peg_fwd"], 0.13)
        self.assertEqual(result["peg_source"], "yahoo_peg_ratio")
        self.assertEqual(result["metric_quality"], "ok")

    @patch("uploader.yf.Ticker")
    def test_currency_matched_listing_supplies_asml_ratios(self, ticker_factory):
        adr = Mock()
        adr.info = {
            "currentPrice": 1784.87,
            "forwardPE": 30.89,
            "trailingPE": 56.82,
            "forwardEps": 57.79,
            "trailingEps": 31.41,
            "pegRatio": 2.65,
            "priceToBook": 1606.0,
            "priceToSalesTrailing12Months": 1138.0,
            "currency": "USD",
            "financialCurrency": "EUR",
        }
        adr.growth_estimates = pd.DataFrame()
        adr.earnings_history = pd.DataFrame()
        adr.history.return_value = pd.DataFrame()
        primary = Mock()
        primary.info = {
            "priceToBook": 30.27,
            "priceToSalesTrailing12Months": 17.56,
        }
        ticker_factory.side_effect = [adr, primary]

        result = uploader.get_us_stock_data("ASML", "ASML.AS")

        self.assertEqual(result["pbr"], 30.27)
        self.assertEqual(result["ps"], 17.56)
        self.assertEqual(result["fundamental_ticker"], "ASML.AS")

    def test_forward_peg_rejects_non_positive_growth(self):
        self.assertIsNone(uploader.calculate_forward_peg(10, 0))
        self.assertIsNone(uploader.calculate_forward_peg(10, -5))

    def test_earnings_surprise_ratio_over_100_pct_keeps_unit(self):
        ticker = Mock()
        ticker.earnings_history = pd.DataFrame(
            {"surprisePercent": [1.25]}, index=["latest"]
        )

        self.assertEqual(uploader.get_us_earnings_surprise(ticker), 125.0)

    @patch("uploader.calculate_target_trend", return_value={})
    @patch("uploader.calculate_eps_trend", return_value={})
    def test_kr_signal_keeps_forward_per_as_consensus_support(self, _eps_trend, _target_trend):
        data = {
            "code": "000660",
            "peg_fwd": None,
            "peg_quality": "unavailable_non_positive_forward_growth",
            "per_ttm": 20.0,
            "per_fwd": 7.0,
            "per_source": "fnguide_multi_year_consensus",
            "eps_growth": 436.5,
            "eps_growth_quality": "extreme_growth_not_for_signal",
            "target_gap": 10.0,
            "pbr": 8.0,
            "rsi": 50.0,
            "vol_ratio": 1.0,
        }

        step1, _, _ = uploader.check_stock_signal(data, "semiconductor", {}, region="kr")

        self.assertTrue(step1)
        self.assertEqual(data["buy_level"], "candidate")

    @patch("uploader.calculate_target_trend", return_value={})
    @patch("uploader.calculate_eps_trend", return_value={})
    def test_kr_signal_uses_forward_cagr_instead_of_base_effect_yoy(self, _eps_trend, _target_trend):
        data = {
            "peg_fwd": 0.35,
            "peg_quality": "multi_year_forward_consensus",
            "per_fwd": 5.85,
            "per_source": "fnguide_multi_year_consensus",
            "forward_eps_cagr": 16.5,
            "eps_growth": 433.9,
            "eps_growth_quality": "extreme_growth_not_for_signal",
            "target_gap": 20.0,
            "pbr": 7.0,
            "rsi": 50.0,
            "vol_ratio": 1.0,
        }

        step1, _, _ = uploader.check_stock_signal(data, "semiconductor", {}, region="kr")

        self.assertTrue(step1)
        self.assertIn("forward_eps_cagr", data["selection_hit_details"])

    @patch("uploader.calculate_target_trend", return_value={})
    @patch("uploader.calculate_eps_trend", return_value={})
    def test_negative_forward_cagr_blocks_kr_per_fallback(self, _eps_trend, _target_trend):
        data = {
            "peg_fwd": None,
            "peg_quality": "unavailable_non_positive_forward_growth",
            "per_fwd": 7.56,
            "per_source": "fnguide_multi_year_consensus",
            "forward_eps_cagr": -5.3,
            "eps_fwd": 4804.0,
            "target_gap": 75.0,
            "pbr": 0.52,
            "div_yield": 1.82,
            "rsi": 50.0,
            "vol_ratio": 1.0,
        }

        step1, _, _ = uploader.check_stock_signal(data, "growth", {}, region="kr")

        self.assertFalse(step1)

    @patch("uploader.calculate_target_trend", return_value={})
    @patch("uploader.calculate_eps_trend", return_value={})
    def test_formula_warning_excludes_peg_from_signal(self, _eps_trend, _target_trend):
        data = {
            "peg_fwd": 0.1,
            "peg_quality": "provider_reported",
            "metric_warnings": ["peg:formula_mismatch"],
            "eps_fwd": 1.0,
            "rsi": 50.0,
            "vol_ratio": 2.0,
            "rev_growth": 30.0,
            "earnings_surprise_pct": 10.0,
            "target_gap": 20.0,
        }

        step1, _, _ = uploader.check_stock_signal(data, "semiconductor", {}, region="us")

        self.assertFalse(step1)

    @patch("uploader._get_published_payload")
    def test_kospi_uses_last_published_close_when_fetch_fails(self, published):
        published.return_value = {
            "updated": "07월 15일 15:31",
            "kospi": {"price": 3000.0, "ma20": 2950.0, "above_ma20": True},
        }

        result = uploader._get_cached_macro_value("kospi")

        self.assertEqual(result["price"], 3000.0)
        self.assertTrue(result["is_stale"])
        self.assertEqual(result["stale_as_of"], "07월 15일 15:31")

    @patch("uploader.db")
    def test_publish_patch_does_not_overwrite_with_none_or_empty_lists(self, firestore_db):
        document = Mock()
        firestore_db.collection.return_value.document.return_value = document

        uploader.publish_payload_patch({
            "kr_stock": [],
            "kospi": None,
            "usdkrw": {"current": 1400.0},
            "updated": "07월 15일 15:31",
        })

        document_calls = firestore_db.collection.return_value.document.call_args_list
        self.assertEqual([args.args[0] for args in document_calls], ["data", "last_good"])
        self.assertEqual(document.set.call_count, 2)
        saved, = document.set.call_args.args
        self.assertNotIn("kr_stock", saved)
        self.assertNotIn("kospi", saved)
        self.assertEqual(saved["usdkrw"]["current"], 1400.0)
        self.assertTrue(document.set.call_args.kwargs["merge"])

    @patch("uploader.get_snapshot_history")
    def test_snapshot_fallback_searches_each_field_across_dates(self, history):
        history.return_value = [
            {"date": "20260714", "price": 100},
            {"date": "20260713", "eps_fwd": 20},
            {"date": "20260712", "target_price": 130},
        ]
        data = {"price": 101, "eps_fwd": None, "target_price": None}

        result = uploader.merge_missing_from_snapshot(
            "005930", data, ("price", "eps_fwd", "target_price")
        )

        self.assertEqual(result["price"], 101)
        self.assertEqual(result["eps_fwd"], 20)
        self.assertEqual(result["target_price"], 130)
        self.assertEqual(result["stale_field_sources"]["eps_fwd"], "20260713")
        self.assertEqual(result["stale_field_sources"]["target_price"], "20260712")

    @patch("uploader.get_snapshot_history")
    def test_kr_derived_metrics_are_not_mixed_with_old_snapshot(self, history):
        history.return_value = [{
            "date": "20260716",
            "peg_fwd": 5.15,
            "target_gap": 99.0,
            "per_fwd": 12.0,
            "forward_eps_cagr": 20.0,
            "forward_eps_cagr_years": 2,
        }]
        current = {"per_fwd": 10.0, "forward_eps_cagr": 25.0, "forward_eps_cagr_years": 2}
        fallback_fields = tuple(
            field for field in uploader.KR_STOCK_SNAPSHOT_FIELDS
            if field not in uploader.KR_STOCK_DERIVED_FIELDS
        )

        result = uploader.merge_missing_from_snapshot("005930", current, fallback_fields)
        uploader.apply_kr_peg(result)

        self.assertEqual(result["peg_fwd"], 0.4)
        self.assertNotEqual(result.get("target_gap"), 99.0)
        self.assertNotIn("peg_fwd", result.get("stale_filled_fields", []))

    @patch("uploader.db")
    def test_snapshot_save_merges_only_non_missing_fields(self, firestore_db):
        document = Mock()
        firestore_db.collection.return_value.document.return_value.collection.return_value.document.return_value = document

        uploader.save_snapshot("005930", {
            "price": 100,
            "eps_fwd": None,
            "data_as_of": "20260714",
            "is_stale": False,
        })

        saved, = document.set.call_args.args
        self.assertEqual(saved["price"], 100)
        self.assertNotIn("eps_fwd", saved)
        self.assertTrue(document.set.call_args.kwargs["merge"])

    def test_duplicate_records_are_removed_by_code_or_name(self):
        records = [
            {"code": "005930", "name": "삼성전자"},
            {"code": "005930", "name": "삼성전자 우"},
            {"code": "000660", "name": " 삼성 전자 "},
            {"code": "034020", "name": "두산에너빌리티"},
        ]

        result = uploader.dedupe_records(records)

        self.assertEqual([record["code"] for record in result], ["005930", "034020"])

    def test_monthly_distribution_target_keeps_prelisting_etf(self):
        etf = {
            "name": "KODEX 200커버드콜액티브",
            "ticker_krx": "0219E0",
            "ticker_yf": "0219E0.KS",
            "distribution_target_yield_monthly": 2.0,
        }
        ticker = Mock()
        ticker.history.return_value = pd.DataFrame()

        with (
            patch("uploader.get_naver_etf_metrics", return_value={}),
            patch("uploader.yf.Ticker", return_value=ticker),
            patch("uploader.requests.get", return_value=FakeResponse({})),
        ):
            result = uploader.get_etf_data(etf)

        self.assertEqual(result["distribution_target_yield_monthly"], 2.0)
        self.assertEqual(result["distribution_target_yield_annual"], 24.0)
        self.assertEqual(result["expected_monthly_dividend_5m"], 100000)
        self.assertEqual(result["expected_dividend_source"], "target_distribution_yield")
        self.assertNotIn("price", result)

    def test_ma_status_ignores_missing_latest_close(self):
        closes = [100 + index for index in range(25)] + [float("nan")]
        hist = pd.DataFrame({"Close": closes})

        result = uploader._calc_ma_status(hist, period=20)

        self.assertEqual(result["price"], 124.0)
        self.assertIsNotNone(result["ma20"])

    @patch("uploader.time.sleep")
    @patch("uploader._get_kis_json")
    def test_daily_chart_keeps_stock_when_current_quote_fails(self, get_kis_json, _sleep):
        candles = [
            {"stck_clpr": str(70000 - index * 100), "acml_vol": str(1000 + index * 10)}
            for index in range(30)
        ]
        get_kis_json.side_effect = [
            {"rt_cd": "1", "msg_cd": "EGW00201"},
            {"rt_cd": "0", "output2": candles},
        ]

        with patch("uploader.get_naver_stock_quote", return_value={}):
            result = uploader.get_kr_stock_data("token", "000660")

        self.assertEqual(result["price"], 70000)
        self.assertEqual(result["volume"], 1000)
        self.assertIsNotNone(result["rsi"])
        self.assertIn("vol_ratio", result)

    @patch("uploader.get_naver_stock_quote")
    def test_naver_quote_keeps_kr_price_when_kis_token_is_missing(self, naver_quote):
        naver_quote.return_value = {
            "price": 130300,
            "price_source": "naver_mobile_realtime",
            "price_market_status": "CLOSE",
        }

        result = uploader.get_kr_stock_data(None, "267270")

        self.assertEqual(result["price"], 130300)
        self.assertEqual(result["price_source"], "naver_mobile_realtime")

    @patch("uploader.requests.get")
    def test_naver_mobile_exchange_rate_is_normalized(self, requests_get):
        requests_get.return_value = FakeResponse({
            "result": {
                "closePrice": "1,380.50",
                "compareToPreviousClosePrice": "2.50",
                "fluctuationsRatio": "0.18",
                "compareToPreviousPrice": {"text": "상승", "name": "RISING"},
            }
        })

        result = uploader._get_usdkrw_from_naver_api()

        self.assertEqual(result["current"], 1380.5)
        self.assertEqual(result["prev"], 1378.0)
        self.assertEqual(result["change_pct"], 0.18)
        self.assertEqual(result["source"], "naver_mobile")

    def test_nav_discount_is_recorded_as_etf_signal_reason(self):
        data = {"rsi": 70, "nav_discount": -0.8, "band_pct": 80}

        step1, step2, reason = uploader.check_etf_signal(data, {})

        self.assertTrue(step1)
        self.assertFalse(step2)
        self.assertIsNone(reason)
        self.assertEqual(data["buy_level"], "candidate")
        self.assertEqual(data["selection_hit_details"], ["nav_discount"])
        self.assertEqual(data["nav_discount_threshold"], -0.5)


if __name__ == "__main__":
    unittest.main()
