import unittest
import re
from pathlib import Path


class NotifierSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parent
        cls.source = (root / "notifier.py").read_text(encoding="utf-8")
        cls.workflow = (root / ".github/workflows/event_shock_alert.yml").read_text(
            encoding="utf-8"
        )
        cls.messaging_worker = (
            root / "public" / "firebase-messaging-sw.js"
        ).read_text(encoding="utf-8")

    def test_token_record_does_not_overwrite_notification_payload(self):
        send_push_source = self.source[
            self.source.index("def send_push"):self.source.index("def send_due_event_shock_alerts")
        ]
        self.assertIn("token_record = doc.to_dict()", send_push_source)
        self.assertIsNone(re.search(r"^\s*data = doc\.to_dict\(\)", send_push_source, re.MULTILINE))
        self.assertIn('data=alert["data"]', self.source)

    def test_event_alert_requires_delivery_before_marking_sent(self):
        claim = self.source.index("claimed = _claim_event_shock(")
        delivered = self.source.index("delivered = send_push(")
        delivery_gate = self.source.index("if delivered <= 0:", delivered)
        state_write = self.source.index('"status": "delivered"', delivery_gate)
        self.assertLess(claim, delivered)
        self.assertLess(delivered, delivery_gate)
        self.assertLess(delivery_gate, state_write)

    def test_dedup_state_read_failure_is_fail_closed(self):
        dedup_error = self.source.index("이벤트 쇼크 알림 claim 실패")
        continue_after_error = self.source.index("continue", dedup_error)
        delivered = self.source.index("delivered = send_push(", continue_after_error)
        self.assertLess(continue_after_error, delivered)

    def test_alert_workflow_runs_at_0700_kst_and_cleans_credentials(self):
        self.assertIn("- cron: '0 22 * * *'", self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("run: rm -f firebase-key.json", self.workflow)

    def test_alert_workflow_has_read_only_repository_permission(self):
        self.assertRegex(self.workflow, r"permissions:\s+contents: read")
        self.assertIn("persist-credentials: false", self.workflow)

    def test_candidate_is_not_a_buy_push_type(self):
        buy_section = self.source[
            self.source.index("# 1~2. 강력 매수 신규 진입"):
            self.source.index("# === RSI 상태 저장")
        ]
        self.assertNotIn("buy_zone_entry", buy_section)
        self.assertNotIn("🟢 매수 후보:", buy_section)

    def test_web_notification_is_not_manually_displayed_twice(self):
        self.assertNotIn("onBackgroundMessage", self.messaging_worker)
        self.assertNotIn("showNotification", self.messaging_worker)
        self.assertIn("messaging.WebpushNotification(", self.source)
        self.assertIn("tag=tag", self.source)
        self.assertIn('"apns-collapse-id": tag', self.source)

    def test_buy_alert_state_is_claimed_transactionally(self):
        self.assertIn("@firestore.transactional", self.source)
        buy_section = self.source[
            self.source.index("# 1~2. 강력 매수 신규 진입"):
            self.source.index("# === RSI 상태 저장")
        ]
        self.assertLess(
            buy_section.index("alert = _claim_buy_alert("),
            buy_section.index("notification = build_buy_notification"),
        )

    def test_buy_delivery_result_is_auditable(self):
        self.assertIn('"last_delivery_status": "delivered" if delivered > 0 else "failed"', self.source)
        self.assertIn('"last_delivery_sample_id": sample_id', self.source)

    def test_only_transition_and_verified_shock_push_paths_remain(self):
        self.assertNotIn('"type": "crisis"', self.source)
        self.assertNotIn('"type": "info"', self.source)
        self.assertNotIn('"type": "vix_reversal"', self.source)
        result_section = self.source[
            self.source.index("def send_due_event_result_alerts"):
            self.source.index("# ============================================================", self.source.index("def send_due_event_result_alerts"))
        ]
        self.assertNotIn("send_push(", result_section)
        self.assertIn("return 0", result_section)

    def test_event_shock_claim_is_transactional_and_failures_are_retryable(self):
        claim = self.source[self.source.index("def _claim_event_shock"):]
        self.assertIn('previous_state.get("status") in {"claimed", "delivered"}', claim)
        self.assertIn('"status": "claimed"', claim)
        self.assertIn("def _release_event_shock_claim", claim)
        self.assertIn('"status": "retryable"', claim)


if __name__ == "__main__":
    unittest.main()
