"""기기 수신 설정이 모든 공통 push 발송 경로에 적용되는지 검증한다."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from datetime import datetime, timedelta, timezone
from notification_policy import push_recipient_enabled


class PushPreferenceTests(unittest.TestCase):
    def test_legacy_approved_recipient_keeps_existing_behavior(self):
        self.assertTrue(push_recipient_enabled({"approved": True}))

    def test_approval_and_both_preference_versions_are_enforced(self):
        for record in (
            {}, {"approved": False}, {"approved": "true"},
            {"approved": True, "push_enabled": False},
            {"approved": True, "notifications_enabled": False},
            {"approved": True, "push_enabled": "true"},
            {"approved": True, "push_enabled": None},
        ):
            with self.subTest(record=record):
                self.assertFalse(push_recipient_enabled(record))
        self.assertTrue(push_recipient_enabled({"approved": True, "push_enabled": True}))
        self.assertTrue(push_recipient_enabled({
            "approved": True, "push_enabled": True, "notifications_enabled": False,
        }))

    def test_common_sender_filters_opt_out_before_calling_messaging(self):
        # 실제 Firebase·서비스계정 초기화 없이 실제 send_push 함수만 실행한다.
        source = ast.parse(Path(__file__).with_name("notifier.py").read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "send_push")
        records = [
            SimpleNamespace(id="fixture-approved", to_dict=lambda: {"approved": True, "push_enabled": True}),
            SimpleNamespace(id="fixture-off", to_dict=lambda: {"approved": True, "push_enabled": False}),
            SimpleNamespace(id="fixture-legacy-off", to_dict=lambda: {"approved": True, "notifications_enabled": False}),
            SimpleNamespace(id="fixture-pending", to_dict=lambda: {"approved": False}),
        ]
        database = Mock()
        database.collection.return_value.stream.return_value = records
        messaging = SimpleNamespace(**{
            name: Mock(side_effect=lambda **kwargs: kwargs)
            for name in ("Message", "Notification", "WebpushConfig", "WebpushNotification", "WebpushFCMOptions", "AndroidConfig", "AndroidNotification", "APNSConfig", "APNSPayload", "Aps")
        }, send=Mock())
        namespace = {
            "datetime": datetime, "timezone": timezone, "PUSH_TTL": timedelta(minutes=10),
            "db": database, "messaging": messaging, "push_recipient_enabled": push_recipient_enabled,
            "os": SimpleNamespace(getenv=lambda name: "0"),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "notifier.py", "exec"), namespace)
        sent = namespace["send_push"]("fixture notice", "fixture body", data={"type": "cycle_interrupt"})
        self.assertEqual(sent, 1)
        self.assertEqual(messaging.send.call_count, 1)
        self.assertEqual(messaging.send.call_args.args[0]["token"], "fixture-approved")
        self.assertEqual(messaging.send.call_args.args[0]["data"], {"type": "cycle_interrupt"})


if __name__ == "__main__":
    unittest.main()
