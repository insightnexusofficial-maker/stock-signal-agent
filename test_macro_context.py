import json
import io
import os
import unittest
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import requests

import macro_context as macro

os.environ.setdefault("SAYO_SKIP_FIREBASE_INIT", "1")
import uploader


NOW = datetime.fromisoformat("2026-10-24T16:00:00+09:00")
CSV = """observation_date,DGS2,DFII10,DTWEXBGS,NFCI,ICSA,CPILFESL,PCEPILFE,UNRATE
2026-06-01,.,.,.,.,.,320,120,4.0
2026-07-01,.,.,.,.,.,321,121,4.1
2026-08-01,.,.,.,.,.,322,122,4.2
2026-09-01,.,.,.,.,.,323,123,4.3
2026-10-02,.,.,.,.,210000,.,.,.
2026-10-09,4.1,2.1,120.1,-0.5,220000,.,.,.
2026-10-16,4.2,2.2,121.2,-0.4,230000,.,.,.
2026-10-22,4.3,2.3,.,.,.,.,.,.
2026-10-23,4.4,2.4,.,.,240000,.,.,.
"""


def response(text=CSV):
    item = Mock()
    item.text = text
    payload = text.encode("utf-8") if isinstance(text, str) else text
    item.iter_content.return_value = [payload]
    item.raise_for_status.return_value = None
    return item


class MacroContextTests(unittest.TestCase):
    def test_csv_keeps_dates_and_individual_release_lag(self):
        items = macro.parse_fred_csv(CSV, list(macro.SERIES), NOW)
        self.assertEqual(items["us_2y"]["observed_at"], "2026-10-23")
        self.assertEqual(items["usd_broad"]["observed_at"], "2026-10-16")
        self.assertEqual(items["nfci"]["observed_at"], "2026-10-16")
        self.assertTrue(all(item["data_status"] == "fresh" for item in items.values()))
        self.assertEqual(items["usd_broad"]["publication_frequency"], "weekly")
        self.assertNotEqual(items["nfci"]["observed_at"], items["nfci"]["retrieved_at"])

    def test_csv_rejects_html_and_missing_columns(self):
        for text in ("<html>upstream unavailable</html>", "DATE,OTHER\n2026-10-23,1"):
            with self.assertRaises(ValueError):
                macro.parse_fred_csv(text, ["us_2y"], NOW)

    def test_csv_ignores_empty_nonfinite_and_future_observations(self):
        text = "DATE,DGS2\n2026-10-22,.\n2026-10-23,NaN\n2026-10-24,inf\n2026-10-26,9.9\n"
        self.assertEqual(macro.parse_fred_csv(text, ["us_2y"], NOW), {})

    def test_history_is_sorted_deduplicated_and_bounded(self):
        points = [{"date": f"2026-10-{day:02}", "value": day} for day in range(1, 25)]
        points += [{"date": "2026-10-24", "value": 99}, {"date": "bad", "value": 1}]
        history = macro.compact_history(points, 3)
        self.assertEqual([item["date"] for item in history], ["2026-10-22", "2026-10-23", "2026-10-24"])
        self.assertEqual(history[-1]["value"], 99)

    def test_saturday_uses_friday_close_without_stale_warning(self):
        item = macro.annotate_market_value({"current": 19, "observed_at": "2026-10-23"}, "vix", NOW)
        self.assertEqual(item["data_status"], "market_closed")
        self.assertFalse(item["is_stale"])
        self.assertEqual(item["observed_at"], "2026-10-23")

    def test_previous_day_during_open_session_is_delayed_not_closed(self):
        now = datetime.fromisoformat("2026-10-23T23:00:00+09:00")
        item = macro.annotate_market_value({"current": 19, "observed_at": "2026-10-22"}, "vix", now)
        self.assertEqual(item["data_status"], "delayed")
        self.assertTrue(item["is_stale"])
        self.assertFalse(macro.market_value_usable(item, "current"))

    def test_us_open_time_and_kr_close_time_are_not_assumed_equal(self):
        preopen = datetime.fromisoformat("2026-10-23T22:10:00+09:00")
        self.assertEqual(macro.annotate_market_value({"current": 19, "observed_at": "2026-10-22"}, "vix", preopen)["data_status"], "market_closed")
        kr_after_close = datetime.fromisoformat("2026-10-23T15:40:00+09:00")
        self.assertEqual(macro.annotate_market_value({"price": 3000, "observed_at": "2026-10-23"}, "kospi", kr_after_close)["data_status"], "market_closed")

    def test_weekday_holiday_is_not_invented_and_unknown_timestamp_is_not_success(self):
        monday = datetime.fromisoformat("2026-10-26T23:00:00+09:00")
        self.assertEqual(macro.annotate_market_value({"current": 19, "observed_at": "2026-10-23"}, "vix", monday)["data_status"], "delayed")
        self.assertNotIn("last_success_at", macro.annotate_market_value({"current": 19}, "vix", monday))

    def test_mixed_frequency_zip_parses_requested_series_without_filesystem_access(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("daily.csv", "observation_date,DGS2,DFII10\n2026-10-23,4.4,2.4\n")
            bundle.writestr("monthly.csv", "DATE,CPILFESL,UNRATE\n2026-09-01,323,4.3\n")
            bundle.writestr("README.txt", "public archive metadata")
        items = macro.parse_fred_payload(archive.getvalue(), list(macro.SERIES), NOW)
        self.assertEqual(set(items), {"us_2y", "us_real_10y", "core_cpi", "unemployment"})
        self.assertEqual(items["core_cpi"]["reference_period"], "2026-09")

    def test_zip_invalid_member_does_not_destroy_other_series(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("one.csv", "DATE,DGS2\n2026-10-23,4.4\n")
            bundle.writestr("unavailable.csv", "upstream HTML error")
        self.assertEqual(set(macro.parse_fred_payload(archive.getvalue(), ["us_2y", "nfci"], NOW)), {"us_2y"})

    def test_csv_missing_one_series_returns_partial_instead_of_failing_all(self):
        items = macro.parse_fred_csv("DATE,DGS2\n2026-10-23,4.4\n", ["us_2y", "nfci"], NOW)
        self.assertEqual(set(items), {"us_2y"})

    def test_download_and_zip_expansion_are_bounded(self):
        with patch.object(macro, "MAX_DOWNLOAD_BYTES", 20):
            with self.assertRaises(ValueError):
                macro.parse_fred_payload(b"A" * 21, ["us_2y"], NOW)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("oversize.csv", "A" * 100)
        with patch.object(macro, "MAX_EXPANDED_BYTES", 50):
            with self.assertRaises(ValueError):
                macro.parse_fred_payload(archive.getvalue(), ["us_2y"], NOW)

    def test_duplicate_archive_series_and_malformed_zip_are_rejected(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            for name in ("first.csv", "second.csv"):
                bundle.writestr(name, "DATE,DGS2\n2026-10-23,4.4\n")
        for payload in (archive.getvalue(), b"PK invalid archive"):
            with self.assertRaises(ValueError):
                macro.parse_fred_payload(payload, ["us_2y"], NOW)

    def test_cache_retains_original_source_and_observation(self):
        original = {"current": 19, "observed_at": "2026-10-08", "last_success_at": "2026-10-09T08:00:00+09:00", "source": "yahoo"}
        item = macro.annotate_market_value(original, "vix", NOW, from_cache=True)
        again = macro.annotate_market_value(item, "vix", NOW + timedelta(days=1), from_cache=True)
        self.assertEqual(again["observed_at"], original["observed_at"])
        self.assertEqual(again["stale_as_of"], original["observed_at"])
        self.assertEqual(again["last_success_at"], original["last_success_at"])
        self.assertEqual(again["original_source"], "yahoo")
        self.assertEqual(again["data_status"], "stale")

    def test_missing_source_date_is_not_retrieval_date(self):
        item = macro.annotate_market_value({"current": 1400}, "usdkrw", NOW)
        self.assertIsNone(item["observed_at"])
        self.assertEqual(item["data_status"], "unavailable")
        self.assertFalse(macro.market_value_usable(item, "current"))

    def test_timeout_uses_previous_value_without_rejuvenation(self):
        previous = macro.parse_fred_csv(CSV, list(macro.SERIES), NOW - timedelta(days=1))
        fetch = Mock(side_effect=requests.Timeout("synthetic timeout"))
        errors = []
        items = macro.collect_reference_series(previous, NOW, fetch, errors.append)
        self.assertEqual(items["us_2y"]["observed_at"], previous["us_2y"]["observed_at"])
        self.assertEqual(items["us_2y"]["last_success_at"], previous["us_2y"]["last_success_at"])
        self.assertEqual(items["us_2y"]["data_status"], "stale")
        self.assertEqual(len(errors), 1)

    def test_http_error_is_categorized_and_empty_cache_remains_unavailable(self):
        fetch = Mock(return_value=response())
        fetch.return_value.raise_for_status.side_effect = requests.HTTPError("synthetic upstream error")
        errors = []
        items = macro.collect_reference_series({}, NOW, fetch, errors.append)
        self.assertTrue(all(item["data_status"] == "unavailable" for item in items.values()))
        self.assertTrue(all("current" not in item for item in items.values()))
        self.assertEqual(len(errors), 1)
        fetch.return_value.close.assert_called_once()

    def test_empty_valid_csv_preserves_each_old_series(self):
        previous = macro.parse_fred_csv(CSV, list(macro.SERIES), NOW - timedelta(days=1))
        fetch = Mock(return_value=response("DATE,DGS2,DFII10,DTWEXBGS,NFCI,ICSA,CPILFESL,PCEPILFE,UNRATE\n2026-10-23,.,.,.,.,.,.,.,.\n"))
        items = macro.collect_reference_series(previous, NOW, fetch)
        self.assertEqual(items["us_2y"]["current"], previous["us_2y"]["current"])
        self.assertEqual(items["us_2y"]["last_attempt_status"], "failed")

    def test_cached_daily_and_weekly_series_do_not_refetch_every_run(self):
        previous = macro.parse_fred_csv(CSV, list(macro.SERIES), NOW)
        fetch = Mock()
        items = macro.collect_reference_series(previous, NOW + timedelta(minutes=30), fetch)
        fetch.assert_not_called()
        self.assertEqual(items, previous)

    def test_single_request_contains_only_due_series(self):
        previous = macro.parse_fred_csv(CSV, list(macro.SERIES), NOW - timedelta(hours=7))
        fetch = Mock(return_value=response("DATE,DGS2,DFII10\n2026-10-23,4.4,2.4\n"))
        items = macro.collect_reference_series(previous, NOW, fetch)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["params"]["id"], "DGS2,DFII10")
        self.assertEqual(items["nfci"]["last_checked_at"], previous["nfci"]["last_checked_at"])

    def test_failure_retries_after_one_hour_without_recurring_half_hour_fetch(self):
        fetch = Mock(side_effect=requests.Timeout())
        previous = macro.collect_reference_series({}, NOW, fetch)
        macro.collect_reference_series(previous, NOW + timedelta(minutes=30), fetch)
        self.assertEqual(fetch.call_count, 1)
        macro.collect_reference_series(previous, NOW + timedelta(hours=1), fetch)
        self.assertEqual(fetch.call_count, 2)

    def test_reference_context_is_display_only_and_reports_missing_axes(self):
        context = macro.build_market_environment({}, {}, NOW)
        self.assertEqual(context["quality_status"], "unavailable")
        self.assertTrue(context["display_only"])
        self.assertTrue(all(axis["status"] == "pending" for axis in context["axes"]))
        self.assertNotIn("buy_score", json.dumps(context))

    def test_monthly_release_keeps_reference_month_and_annualizes_only_consecutive_months(self):
        items = macro.parse_fred_csv(CSV, list(macro.SERIES), NOW)
        self.assertEqual(items["core_cpi"]["observed_at"], "2026-09-01")
        self.assertEqual(items["core_cpi"]["data_status"], "fresh")
        self.assertAlmostEqual(items["core_cpi"]["trend_3m_annualized_pct"], ((323 / 320) ** 4 - 1) * 100, places=2)
        self.assertEqual(items["initial_claims"]["four_week_average"], 225000)
        self.assertNotIn("trend_3m_annualized_pct", items["us_2y"])

    def test_monthly_stale_and_nonconsecutive_months_are_not_silently_filled(self):
        text = "DATE,CPILFESL\n2026-04-01,310\n2026-05-01,311\n2026-07-01,313\n2026-08-01,314\n"
        item = macro.parse_fred_csv(text, ["core_cpi"], NOW)["core_cpi"]
        self.assertNotIn("trend_3m_annualized_pct", item)
        self.assertEqual(item["data_status"], "fresh")
        late = macro.parse_fred_csv(text, ["core_cpi"], NOW + timedelta(days=40))["core_cpi"]
        self.assertEqual(late["data_status"], "stale")


class UploaderMacroIntegrationTests(unittest.TestCase):
    @patch("uploader._get_published_payload", return_value={})
    @patch("uploader._get_usdkrw_from_naver_html")
    @patch("uploader._get_usdkrw_from_yahoo_chart")
    @patch("uploader._get_usdkrw_from_naver_api")
    @patch("uploader._get_kospi_from_naver_api", return_value=None)
    @patch("uploader.yf.Ticker")
    def test_exchange_fallback_prefers_dated_source_over_first_undated_value(self, ticker, _kospi, naver, yahoo, html, _published):
        ticker.return_value.history.return_value.empty = True
        naver.return_value = {"current": 1400, "source": "naver_mobile"}
        yahoo.return_value = {"current": 1399, "source": "yahoo_chart", "observed_at": NOW.date().isoformat()}
        result = uploader.get_macro_data()
        self.assertEqual(result["usdkrw"]["current"], 1399)
        self.assertEqual(result["usdkrw"]["source"], "yahoo_chart")
        self.assertIsNotNone(result["usdkrw"]["observed_at"])
        html.assert_not_called()

    def test_all_providers_timeout_and_empty_public_cache_produce_unknown(self):
        report = uploader._new_collection_report()
        with (
            patch.object(uploader, "_active_collection_report", report),
            patch.object(uploader.yf, "Ticker", side_effect=requests.Timeout("synthetic source body")),
            patch.object(uploader, "_get_kospi_from_naver_api", side_effect=requests.Timeout()),
            patch.object(uploader, "_get_usdkrw_from_naver_api", side_effect=requests.Timeout()),
            patch.object(uploader, "_get_usdkrw_from_yahoo_chart", side_effect=requests.Timeout()),
            patch.object(uploader, "_get_usdkrw_from_naver_html", side_effect=requests.Timeout()),
            patch.object(uploader, "_get_published_payload", return_value={}),
            patch("sys.stdout", new_callable=io.StringIO) as logs,
        ):
            result = uploader.get_macro_data()
        self.assertTrue(all(value is None for value in result.values()))
        self.assertEqual(uploader.determine_market_mode(result), "unknown")
        self.assertEqual(report["error_count"], 7)
        self.assertNotIn("synthetic source body", logs.getvalue())

    def test_unknown_does_not_default_to_calm(self):
        self.assertEqual(uploader.determine_market_mode({}), "unknown")
        self.assertIsNone(uploader.get_rsi_threshold({"rsi_normal": 40}, "unknown"))
        stale = {"vix": {"current": 40, "is_stale": True}, "qqq": {"price": 100, "above_ma20": False}}
        self.assertEqual(uploader.determine_market_mode(stale), "unknown")

    def test_known_modes_preserve_existing_thresholds(self):
        for vix, above, expected in ((15, True, "normal"), (15, False, "adjust"), (20, True, "adjust"), (25, True, "caution"), (35, True, "panic")):
            context = {"vix": {"current": vix}, "qqq": {"price": 100, "above_ma20": above}}
            self.assertEqual(uploader.determine_market_mode(context), expected)
            self.assertEqual(uploader.get_rsi_threshold(uploader.SECTOR_CRITERIA["semiconductor"], expected), {"normal": 40, "adjust": 35, "caution": 30, "panic": 25}[expected])

    @patch("uploader.calculate_target_trend", return_value={"slope_mom_pct": 10})
    @patch("uploader.calculate_eps_trend", return_value={"slope_mom_pct": 10})
    def test_unknown_preserves_fundamental_candidate_but_holds_timing(self, _eps, _target):
        stock = {"code": "TEST", "rsi": 42, "peg_fwd": .6, "rev_growth": 10, "earnings_surprise_pct": 5, "target_gap": 20}
        step1, step2, _ = uploader.check_stock_signal(stock, "semiconductor", {})
        self.assertTrue(step1)
        self.assertFalse(step2)
        self.assertEqual(stock["buy_level"], "candidate")
        self.assertEqual(stock["timing_status"], "pending_macro")
        self.assertIsNone(stock["rsi_threshold"])

    def test_unknown_etf_keeps_nav_candidate_but_does_not_upgrade(self):
        stock = {"rsi": 25, "nav_discount": -1, "band_pct": 20}
        step1, step2, _ = uploader.check_etf_signal(stock, {})
        self.assertTrue(step1)
        self.assertFalse(step2)
        self.assertEqual(stock["buy_level"], "candidate")

    def test_stale_vix_cannot_create_market_panic(self):
        context = {"vix": {"current": 45, "is_stale": True}, "qqq": {"price": 100, "above_ma20": False}}
        self.assertNotIn("market_panic", uploader.check_crisis_trigger({"code": "TEST"}, context)[0])

    @patch("uploader._get_published_payload")
    def test_cache_does_not_use_document_updated_as_observation(self, published):
        published.return_value = {"updated": "10월 24일 16:01", "kospi": {"price": 3000}}
        item = uploader._get_cached_macro_value("kospi")
        self.assertIsNone(item["observed_at"])
        self.assertIsNone(item["stale_as_of"])

    def test_cycle_details_are_display_only(self):
        stock = {"code": "TEST", "step1": False, "buy_level": "none"}
        company = {"ticker": "TEST", "status": "neutral", "assessment_status": "insufficient", "data_status": "fresh", "coverage": {"publisher_count": 1}, "key_hypotheses": ["고객 투자 유지"], "supporting_evidence_ids": ["e1"]}
        uploader.attach_cycle_context(stock, {"company_cycle": [company], "evidence": [{"id": "e1", "fact": "공식 사실", "source_url": "https://example.invalid"}]})
        self.assertEqual(stock["cycle_assessment_status"], "insufficient")
        self.assertEqual(stock["cycle_evidence"][0]["id"], "e1")
        self.assertFalse(stock["step1"])
        self.assertEqual(stock["buy_level"], "none")


if __name__ == "__main__":
    unittest.main()
