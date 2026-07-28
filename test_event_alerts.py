import unittest
from datetime import datetime

from event_alerts import due_shock_alerts


def feed_with_result(result):
    return {
        "expires_at": "2026-08-20T08:00:00+09:00",
        "quality_gate": {"status": "passed", "mode": "official-only"},
        "shock_policy": {
            "notification_time_kst": "07:00",
            "mode": "objective-official-data-only",
        },
        "recent_results": [result],
    }


class EventAlertTests(unittest.TestCase):
    def result(self):
        return {
            "event_id": "macro-us-cpi-2026-07",
            "event_name": "미국 2026년 7월 CPI",
            "kind": "macro",
            "status": "complete",
            "review_status": "verified",
            "summary": "공식 CPI 발표 확인",
            "shock": {
                "is_shock": True,
                "severity": "shock",
                "rule_id": "macro-inflation-mom-0_6pct",
                "reason": "공식 CPI 전월 대비 변동률 절대값이 0.6% 이상",
                "audit_passed": True,
                "notify_at": "2026-08-13T07:00:00+09:00",
            },
        }

    def test_verified_shock_is_due_at_seven(self):
        alerts = due_shock_alerts(
            feed_with_result(self.result()),
            now=datetime.fromisoformat("2026-08-13T07:00:00+09:00"),
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["data"]["type"], "event_shock")

    def test_alert_is_not_sent_before_seven(self):
        alerts = due_shock_alerts(
            feed_with_result(self.result()),
            now=datetime.fromisoformat("2026-08-13T06:59:59+09:00"),
        )

        self.assertEqual(alerts, [])

    def test_alert_expires_after_grace_window(self):
        alerts = due_shock_alerts(
            feed_with_result(self.result()),
            now=datetime.fromisoformat("2026-08-13T11:00:01+09:00"),
        )

        self.assertEqual(alerts, [])

    def test_pending_or_unaudited_result_is_not_alerted(self):
        result = self.result()
        result["shock"]["audit_passed"] = False

        self.assertEqual(
            due_shock_alerts(
                feed_with_result(result),
                now=datetime.fromisoformat("2026-08-13T07:00:00+09:00"),
            ),
            [],
        )

    def test_non_official_feed_is_rejected(self):
        feed = feed_with_result(self.result())
        feed["quality_gate"]["mode"] = "mixed"

        self.assertEqual(
            due_shock_alerts(
                feed,
                now=datetime.fromisoformat("2026-08-13T07:00:00+09:00"),
            ),
            [],
        )

    def test_expired_feed_is_rejected(self):
        feed = feed_with_result(self.result())
        feed["expires_at"] = "2026-08-13T06:59:59+09:00"

        self.assertEqual(
            due_shock_alerts(
                feed,
                now=datetime.fromisoformat("2026-08-13T07:00:00+09:00"),
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
