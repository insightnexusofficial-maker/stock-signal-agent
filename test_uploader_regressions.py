import unittest
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

        result = uploader.get_kr_stock_data("token", "000660")

        self.assertEqual(result["price"], 70000)
        self.assertEqual(result["volume"], 1000)
        self.assertIsNotNone(result["rsi"])
        self.assertIn("vol_ratio", result)

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
