"""거시 결측 보류와 복구를 실제 함수의 합성 상태로 검증한다. Firebase 미접속."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from notification_policy import build_buy_notification, evaluate_buy_alert
from test_notifier_claims import FakeDatabase, FakeStateRef


def previous_state(**overrides):
    result = {
        "schema_version": 2, "sample_id": "valid-1", "prev_rsi": 41,
        "in_zone": True, "strong_active": True,
        "last_claimed_type": "strong_buy", "observed_at": "valid-observation",
    }
    result.update(overrides)
    return result


def pending_stock(**overrides):
    result = {
        "code": "FIXTURE", "step1": True, "buy_level": "candidate", "rsi": 30,
        "rsi_threshold": None, "in_buy_zone": False,
        "timing_status": "pending_macro", "market_mode": "unknown",
    }
    result.update(overrides)
    return result


def recovered_stock(**overrides):
    result = {
        "code": "FIXTURE", "name": "합성 종목", "price": 100,
        "step1": True, "buy_level": "strong", "rsi": 42,
        "rsi_threshold": 40, "rsi_zone_upper": 50, "in_buy_zone": True,
        "timing_status": "ready", "market_mode": "normal",
    }
    result.update(overrides)
    return result


def notifier_functions(database):
    """Compile only production decision functions, without importing Firebase."""
    tree = ast.parse(Path(__file__).with_name("notifier.py").read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"check_and_notify", "_claim_buy_alert"}]
    for node in functions:
        node.decorator_list = []
    namespace = {
        "db": database, "os": SimpleNamespace(getenv=lambda name: "0"),
        "_mode_emoji": lambda mode: mode,
        "is_kr_buy_alert_session": lambda now: True,
        "is_us_buy_alert_session": lambda now: True,
        "evaluate_buy_alert": evaluate_buy_alert,
        "build_buy_notification": build_buy_notification,
        "send_push": Mock(return_value=1),
        "firestore": SimpleNamespace(SERVER_TIMESTAMP="synthetic-server-time"),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), "notifier.py", "exec"), namespace)
    return namespace


class PendingMacroPolicyTests(unittest.TestCase):
    def test_either_pending_flag_preserves_entire_previous_state_without_alias(self):
        for flags in (
            {"timing_status": "pending_macro", "market_mode": "normal"},
            {"timing_status": "ready", "market_mode": "unknown"},
        ):
            with self.subTest(flags=flags):
                previous = previous_state()
                alert, state = evaluate_buy_alert(pending_stock(**flags), previous,
                                                 fallback_prev_rsi=2, sample_id="missing-2")
                self.assertIsNone(alert)
                self.assertEqual(state, previous)
                self.assertIsNot(state, previous)

    def test_unknown_first_observation_does_not_initialize_or_advance_rsi(self):
        alert, state = evaluate_buy_alert(pending_stock(), fallback_prev_rsi=39, sample_id="missing-1")
        self.assertIsNone(alert)
        self.assertEqual(state, {})

    def test_recovery_does_not_rebroadcast_existing_strong(self):
        previous = previous_state()
        _, held = evaluate_buy_alert(pending_stock(), previous, sample_id="missing-2")
        alert, recovered = evaluate_buy_alert(recovered_stock(), held, sample_id="valid-3")
        self.assertIsNone(alert)
        self.assertTrue(recovered["strong_active"])
        self.assertEqual(recovered["prev_rsi"], 42)

    def test_recovery_keeps_real_upward_cross_from_last_valid_baseline(self):
        previous = previous_state(prev_rsi=39, strong_active=False, in_zone=False)
        _, held = evaluate_buy_alert(pending_stock(rsi=45), previous, sample_id="missing-2")
        alert, _ = evaluate_buy_alert(recovered_stock(rsi=41), held, sample_id="valid-3")
        self.assertEqual(alert["type"], "buy_now")
        self.assertEqual(alert["previous_rsi"], 39)


class PendingMacroNotifierTests(unittest.TestCase):
    def test_loop_preserves_signal_and_rsi_state_then_recovers_without_duplicate(self):
        for flags in (
            {"timing_status": "pending_macro", "market_mode": "normal"},
            {"timing_status": "ready", "market_mode": "unknown"},
        ):
            with self.subTest(flags=flags):
                database = FakeDatabase({"collection_finished_at": "missing-2",
                                         "kr_stock": [pending_stock(**flags)]}, {"FIXTURE": 41})
                signal = previous_state()
                database.documents[("state", "buy_zone_FIXTURE")] = FakeStateRef(signal)
                namespace = notifier_functions(database)
                namespace["_claim_buy_alert"] = Mock(wraps=namespace["_claim_buy_alert"])
                namespace["check_and_notify"]()
                namespace["_claim_buy_alert"].assert_not_called()
                namespace["send_push"].assert_not_called()
                self.assertEqual(database.documents[("state", "rsi")].state, {"FIXTURE": 41})
                self.assertEqual(database.documents[("state", "buy_zone_FIXTURE")].state, signal)

                database.documents[("stocks", "data")].state = {
                    "collection_finished_at": "valid-3", "kr_stock": [recovered_stock()]}
                namespace["check_and_notify"]()
                self.assertEqual(namespace["_claim_buy_alert"].call_count, 1)
                namespace["send_push"].assert_not_called()
                self.assertEqual(database.documents[("state", "rsi")].state, {"FIXTURE": 42})
                self.assertTrue(database.documents[("state", "buy_zone_FIXTURE")].state["strong_active"])

    def test_pending_without_previous_rsi_or_signal_does_not_create_baseline(self):
        database = FakeDatabase({"kr_etf": [pending_stock()]})
        namespace = notifier_functions(database)
        namespace["check_and_notify"]()
        self.assertNotIn(("state", "buy_zone_FIXTURE"), database.documents)
        self.assertEqual(database.documents[("state", "rsi")].state, {})
        namespace["send_push"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
