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

    def test_token_record_does_not_overwrite_notification_payload(self):
        send_push_source = self.source[
            self.source.index("def send_push"):self.source.index("def send_due_event_shock_alerts")
        ]
        self.assertIn("token_record = doc.to_dict()", send_push_source)
        self.assertIsNone(re.search(r"^\s*data = doc\.to_dict\(\)", send_push_source, re.MULTILINE))
        self.assertIn('data=alert["data"]', self.source)

    def test_event_alert_requires_delivery_before_marking_sent(self):
        delivered = self.source.index("delivered = send_push(")
        delivery_gate = self.source.index("if delivered <= 0:", delivered)
        state_write = self.source.index('db.collection("state").document(state_id).set', delivery_gate)
        self.assertLess(delivered, delivery_gate)
        self.assertLess(delivery_gate, state_write)

    def test_dedup_state_read_failure_is_fail_closed(self):
        dedup_error = self.source.index("이벤트 알림 중복 방지 상태 확인 실패")
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


if __name__ == "__main__":
    unittest.main()
