import os
import sys
import unittest
from unittest.mock import Mock, patch

from test_notifier_claims import load_notifier_with_fake_firebase


class PushSuppressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notifier = load_notifier_with_fake_firebase()

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("notifier", None)

    def test_qa_guard_precedes_token_reads_claims_and_external_fetches(self):
        notifier = self.notifier
        database, fetch, delivery, claim = Mock(), Mock(), Mock(), Mock()
        with patch.dict(os.environ, {"SAYO_DISABLE_PUSH": "1"}), \
                patch.object(notifier, "db", database), \
                patch.object(notifier.requests, "get", fetch), \
                patch.object(notifier, "_claim_event_shock", claim), \
                patch.object(notifier.messaging, "send", delivery, create=True):
            self.assertEqual(notifier.send_push("synthetic", "synthetic"), 0)
            self.assertEqual(notifier.check_and_notify(), 0)
            self.assertEqual(notifier.send_due_event_shock_alerts(), 0)
            self.assertEqual(notifier.send_due_cycle_interrupt_alerts(), 0)
        database.collection.assert_not_called()
        database.transaction.assert_not_called()
        fetch.assert_not_called()
        delivery.assert_not_called()
        claim.assert_not_called()

    def test_off_guard_preserves_normal_entry_flow(self):
        database = Mock()
        database.collection.return_value.stream.return_value = []
        with patch.dict(os.environ, {"SAYO_DISABLE_PUSH": "0"}), patch.object(self.notifier, "db", database):
            self.assertEqual(self.notifier.send_push("synthetic", "synthetic"), 0)
        database.collection.assert_called_once_with("fcm_tokens")


if __name__ == "__main__":
    unittest.main()
