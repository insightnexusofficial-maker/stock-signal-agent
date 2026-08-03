import unittest
from datetime import datetime

from notification_policy import (
    build_buy_notification,
    evaluate_buy_alert,
    is_kr_buy_alert_session,
)


def initialized_state(**overrides):
    state = {
        "schema_version": 2,
        "sample_id": "sample-0",
        "prev_rsi": 45,
        "in_zone": False,
        "strong_active": False,
    }
    state.update(overrides)
    return state


class NotificationPolicyTests(unittest.TestCase):
    def test_kr_buy_alert_session_uses_kst_regular_hours(self):
        self.assertFalse(is_kr_buy_alert_session(datetime.fromisoformat("2026-08-03T08:59:59+09:00")))
        self.assertTrue(is_kr_buy_alert_session(datetime.fromisoformat("2026-08-03T09:00:00+09:00")))
        self.assertTrue(is_kr_buy_alert_session(datetime.fromisoformat("2026-08-03T15:29:59+09:00")))
        self.assertFalse(is_kr_buy_alert_session(datetime.fromisoformat("2026-08-03T15:30:00+09:00")))

    def test_kr_buy_alert_session_rejects_night_and_weekend(self):
        self.assertFalse(is_kr_buy_alert_session(datetime.fromisoformat("2026-08-04T01:06:00+09:00")))
        self.assertFalse(is_kr_buy_alert_session(datetime.fromisoformat("2026-08-08T10:00:00+09:00")))

    def test_candidate_never_creates_push(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "candidate",
            "rsi": 45,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": True,
        }

        alert, state = evaluate_buy_alert(
            stock,
            initialized_state(prev_rsi=46),
            sample_id="sample-1",
        )

        self.assertIsNone(alert)
        self.assertFalse(state["strong_active"])

    def test_strong_transition_alerts_once(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "strong",
            "rsi": 44,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": True,
        }

        alert, state = evaluate_buy_alert(
            stock,
            initialized_state(prev_rsi=45),
            sample_id="sample-1",
        )
        repeated, _ = evaluate_buy_alert(
            stock,
            state,
            sample_id="sample-2",
        )

        self.assertEqual(alert["type"], "strong_buy")
        self.assertIsNone(repeated)

    def test_now_wins_when_now_and_strong_start_together(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "strong",
            "rsi": 41,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": True,
        }

        alert, state = evaluate_buy_alert(
            stock,
            initialized_state(prev_rsi=35),
            sample_id="sample-1",
        )
        followup, _ = evaluate_buy_alert(
            {**stock, "rsi": 42},
            state,
            sample_id="sample-2",
        )

        self.assertEqual(alert["type"], "buy_now")
        self.assertTrue(state["strong_active"])
        self.assertIsNone(followup)

    def test_etf_now_detects_normal_upward_cross(self):
        etf = {
            "code": "ETF",
            "step1": True,
            "buy_level": "candidate",
            "rsi": 41,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": False,
        }

        alert, _ = evaluate_buy_alert(
            etf,
            initialized_state(prev_rsi=39),
            sample_id="sample-1",
        )

        self.assertEqual(alert["type"], "buy_now")

    def test_jump_above_watch_zone_is_not_now(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "candidate",
            "rsi": 51,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": False,
        }

        alert, _ = evaluate_buy_alert(
            stock,
            initialized_state(prev_rsi=39),
            sample_id="sample-1",
        )

        self.assertIsNone(alert)

    def test_older_sample_cannot_roll_state_back(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "candidate",
            "rsi": 39,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": False,
        }
        current_state = initialized_state(
            sample_id="2026-07-29T23:10:00+09:00",
            prev_rsi=41,
            strong_active=True,
        )

        alert, state = evaluate_buy_alert(
            stock,
            current_state,
            sample_id="2026-07-29T23:00:00+09:00",
        )

        self.assertIsNone(alert)
        self.assertEqual(state, current_state)

    def test_same_sample_cannot_be_claimed_twice(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "candidate",
            "rsi": 41,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": False,
        }
        claimed_state = initialized_state(
            sample_id="2026-07-29T23:10:00+09:00",
            prev_rsi=41,
        )

        alert, state = evaluate_buy_alert(
            stock,
            claimed_state,
            sample_id="2026-07-29T23:10:00+09:00",
        )

        self.assertIsNone(alert)
        self.assertEqual(state, claimed_state)

    def test_stale_sample_does_not_alert_or_advance_signal_state(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "strong",
            "rsi": 41,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": True,
            "is_stale": True,
        }

        alert, state = evaluate_buy_alert(
            stock,
            initialized_state(prev_rsi=39, strong_active=False),
            sample_id="sample-1",
        )

        self.assertIsNone(alert)
        self.assertEqual(state["prev_rsi"], 39)
        self.assertFalse(state["strong_active"])

    def test_first_v2_observation_does_not_broadcast_existing_strong(self):
        stock = {
            "code": "TEST",
            "step1": True,
            "buy_level": "strong",
            "rsi": 35,
            "rsi_threshold": 40,
            "rsi_zone_upper": 50,
            "in_buy_zone": True,
        }

        alert, state = evaluate_buy_alert(
            stock,
            {"in_zone": True},
            sample_id="sample-1",
        )

        self.assertIsNone(alert)
        self.assertTrue(state["strong_active"])

    def test_etf_strong_message_uses_etf_evidence_not_earnings(self):
        stock = {
            "code": "ETF",
            "name": "테스트 ETF",
            "buy_level": "strong",
            "rsi": 35,
            "rsi_threshold": 40,
            "nav_discount": -0.8,
            "band_pct": 42,
            "selection_hit_details": ["rsi_watch", "nav_discount", "band_position"],
            "price": 12340,
        }

        notification = build_buy_notification(
            {"type": "strong_buy", "previous_rsi": 36},
            stock,
            "etf",
        )

        self.assertIn("NAV 대비 0.80% 할인", notification["body"])
        self.assertIn("52주 위치 42.0%", notification["body"])
        self.assertNotIn("EPS", notification["body"])
        self.assertNotIn("목표", notification["body"])


if __name__ == "__main__":
    unittest.main()
