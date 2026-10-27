"""ETF price regression tests: production AST only, synthetic providers, no credentials."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class Column:
    def __init__(self, value):
        self.iloc = self
        self.value = value

    def __getitem__(self, index):
        return self.value


class History:
    index = ["2026-10-27"]

    def __init__(self, close, empty=False):
        self.close = close
        self.empty = empty

    def __len__(self):
        return 0 if self.empty else 1

    def __getitem__(self, key):
        return Column(self.close if key == "Close" else 1000)


def production_namespace(close=126.0, naver=None, snapshots=None, empty=False,
                         distribution_only=False):
    tree = ast.parse(Path(__file__).with_name("uploader.py").read_text())
    names = {
        "_to_float", "_is_missing_value", "_positive_price", "_snapshot_field_missing",
        "get_latest_valid_snapshot", "merge_missing_from_snapshot", "get_etf_data",
        "_validate_etf_price", "_collection_status", "_new_collection_report",
        "_increment_counter", "_record_collection_error", "_sanitize_for_firestore",
    }
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    import re
    etf = {"name": "합성 ETF", "ticker_yf": "SYNTH.KS", "ticker_krx": "SYNTH"}
    def apply_distribution(etf, data):
        if distribution_only:
            data["expected_monthly_dividend_5m"] = 50000

    ns = {
        "re": re, "get_naver_etf_metrics": Mock(return_value=naver or {}),
        "yf": SimpleNamespace(Ticker=Mock(return_value=SimpleNamespace(
            history=Mock(return_value=History(close, empty)), info={}))),
        "market_date_str": lambda stamp: "20261027",
        "apply_configured_etf_distribution_target": apply_distribution,
        "requests": SimpleNamespace(get=Mock(return_value=SimpleNamespace(text="", encoding="utf-8"))),
        "_classify_request_error": lambda error: "synthetic_error",
        "get_snapshot_history": Mock(return_value=list(snapshots or [])),
        "_collection_summary": lambda report: {"expected_counts": {
            "kr_stock": 7, "kr_etf": 15, "us_stock": 22, "macro": 4}},
        "_active_collection_report": None, "dedupe_records": lambda values, **kwargs: values,
        "KR_ETFS": [etf], "KR_ETF_SNAPSHOT_FIELDS": ("price", "price_as_of", "rsi", "nav"),
        "macro": {}, "check_etf_signal": Mock(return_value=(False, False, "")),
        "get_date_str": lambda: "20261028", "save_snapshot": Mock(),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), "uploader.py", "exec"), ns)
    ns["_active_collection_report"] = ns["_new_collection_report"]()
    # Execute the real ETF upload loop in isolation so both fallback branches and
    # final quality validation are exercised, without the rest of the uploader.
    upload = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_upload_data")
    loop = next(node for node in upload.body if isinstance(node, ast.For)
                and isinstance(node.target, ast.Name) and node.target.id == "etf")
    isolated = ast.FunctionDef(name="collect_etfs", args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[ast.Assign(targets=[ast.Name(id="kr_etf_list", ctx=ast.Store())], value=ast.List(elts=[], ctx=ast.Load())),
              loop, ast.Return(value=ast.Name(id="kr_etf_list", ctx=ast.Load()))], decorator_list=[])
    ast.fix_missing_locations(isolated)
    exec(compile(ast.Module(body=[isolated], type_ignores=[]), "uploader.py", "exec"), ns)
    return ns, etf


class EtfPriceQualityTests(unittest.TestCase):
    def test_finite_price_preserves_normal_collection_and_date(self):
        ns, etf = production_namespace(close=126, naver={"price": 125})
        data = ns["get_etf_data"](etf)
        self.assertEqual(data["price"], 126)
        self.assertEqual(data["price_as_of"], "20261027")
        self.assertTrue(ns["_validate_etf_price"](data))
        self.assertEqual(ns["_active_collection_report"]["critical_error_count"], 0)

    def test_invalid_yahoo_prices_allow_valid_naver_fallback(self):
        for close in (float("nan"), float("inf"), -float("inf"), 0, -1, None):
            with self.subTest(close=close):
                ns, etf = production_namespace(close=close, naver={"price": 125})
                data = ns["get_etf_data"](etf)
                self.assertEqual(data["price"], 125)
                self.assertNotIn("price_as_of", data, "invalid Yahoo row must not timestamp Naver price")
                self.assertEqual(ns["_sanitize_for_firestore"](data)["price"], 125)

    def test_fallback_carries_its_own_source_date_only_when_used(self):
        quote = {"price": 125, "price_as_of": "20261026", "price_source": "naver_mobile_daily"}
        ns, etf = production_namespace(close=float("nan"), naver=quote)
        data = ns["get_etf_data"](etf)
        self.assertEqual(data["price_as_of"], "20261026")
        self.assertEqual(data["price_source"], "naver_mobile_daily")
        ns, etf = production_namespace(close=126, naver=quote)
        data = ns["get_etf_data"](etf)
        self.assertEqual(data["price_as_of"], "20261027")
        self.assertEqual(data["price_source"], "yahoo_history")

    def test_empty_yahoo_history_allows_naver_price(self):
        ns, etf = production_namespace(empty=True, naver={"price": 125})
        self.assertEqual(ns["get_etf_data"](etf)["price"], 125)

    def test_invalid_naver_price_is_not_kept_as_a_quote(self):
        for price in (float("nan"), float("inf"), 0, -1, False):
            with self.subTest(price=price):
                ns, etf = production_namespace(empty=True, naver={"price": price})
                self.assertIsNone(ns["get_etf_data"](etf))

    def test_snapshot_fallback_skips_invalid_price_and_preserves_stale(self):
        for close, empty in ((float("nan"), False), (float("inf"), False), (None, True)):
            with self.subTest(close=close, empty=empty):
                ns, _ = production_namespace(close=close, empty=empty, snapshots=[
                    {"date": "20261027", "price": float("inf")},
                    {"date": "20261026", "price": 0},
                    {"date": "20261023", "price": 124, "price_as_of": "20261023"},
                ])
                rows = ns["collect_etfs"]()
                self.assertEqual(rows[0]["price"], 124)
                self.assertTrue(rows[0]["is_stale"])
                self.assertEqual(rows[0]["stale_as_of"], "20261023")
                self.assertEqual(rows[0]["data_as_of"], "20261023")

    def test_distribution_only_row_can_fill_missing_price_from_snapshot(self):
        ns, _ = production_namespace(close=float("nan"), distribution_only=True,
                                     snapshots=[{"date": "20261023", "price": 124}])
        rows = ns["collect_etfs"]()
        self.assertEqual(rows[0]["price"], 124)
        self.assertTrue(rows[0]["is_stale"])
        self.assertIn("price", rows[0]["stale_filled_fields"])
        self.assertEqual(rows[0]["stale_field_sources"]["price"], "20261023")

    def test_final_missing_price_marks_partial_even_with_complete_row_counts(self):
        ns, _ = production_namespace(close=float("nan"), distribution_only=True)
        rows = ns["collect_etfs"]()
        self.assertEqual(len(rows), 1, "preserve the configured distribution calculator row")
        self.assertIsNone(rows[0]["price"])
        self.assertEqual(rows[0]["expected_monthly_dividend_5m"], 50000)
        report = ns["_active_collection_report"]
        self.assertEqual(report["errors_by_stage"], {"etf_price": 1})
        self.assertEqual(report["errors_by_category"], {"missing_price": 1})
        self.assertEqual(ns["_collection_status"](report, {
            "kr_stock": 7, "kr_etf": 15, "us_stock": 22, "macro": 4}), "partial")

    def test_no_price_and_no_distribution_records_quality_error_before_skipping(self):
        ns, _ = production_namespace(empty=True)
        self.assertEqual(ns["collect_etfs"](), [])
        self.assertEqual(ns["_active_collection_report"]["errors_by_stage"], {"etf_price": 1})

    def test_general_missing_semantics_preserve_zero_negative_and_boolean(self):
        ns, _ = production_namespace()
        for value in (None, "", float("nan"), float("inf"), -float("inf"), "NaN"):
            with self.subTest(missing=value):
                self.assertTrue(ns["_is_missing_value"](value))
        for value in (0, -1, False, True, "0", "text"):
            with self.subTest(present=value):
                self.assertFalse(ns["_is_missing_value"](value))

    def test_nonprice_snapshot_zero_and_negative_values_remain_valid(self):
        ns, _ = production_namespace(snapshots=[{"date": "20261023", "rsi": 0, "nav_discount": -0.5}])
        merged = ns["merge_missing_from_snapshot"]("SYNTH", {"rsi": None, "nav_discount": None}, ("rsi", "nav_discount"))
        self.assertEqual(merged["rsi"], 0)
        self.assertEqual(merged["nav_discount"], -0.5)


if __name__ == "__main__":
    unittest.main()
