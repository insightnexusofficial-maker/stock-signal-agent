import importlib
import sys
import types
import unittest
from unittest.mock import patch


class FakeSnapshot:
    def __init__(self, state):
        self.exists = bool(state)
        self._state = dict(state)

    def to_dict(self):
        return dict(self._state)


class FakeStateRef:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def get(self, transaction=None):
        return FakeSnapshot(self.state)


class FakeTransaction:
    def set(self, state_ref, values, merge=False):
        if not merge:
            state_ref.state = {}
        state_ref.state.update(values)


def load_notifier_with_fake_firebase():
    firebase_admin = types.ModuleType("firebase_admin")
    credentials = types.SimpleNamespace(Certificate=lambda path: object())
    firestore = types.SimpleNamespace(
        SERVER_TIMESTAMP=object(),
        client=lambda: object(),
        transactional=lambda function: function,
    )
    messaging = types.SimpleNamespace()
    firebase_admin.credentials = credentials
    firebase_admin.firestore = firestore
    firebase_admin.messaging = messaging
    firebase_admin.get_app = lambda: object()
    firebase_admin.initialize_app = lambda credential: object()

    fake_modules = {
        "firebase_admin": firebase_admin,
        "firebase_admin.credentials": credentials,
        "firebase_admin.firestore": firestore,
        "firebase_admin.messaging": messaging,
    }
    sys.modules.pop("notifier", None)
    with patch.dict(sys.modules, fake_modules):
        return importlib.import_module("notifier")


class EventShockClaimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notifier = load_notifier_with_fake_firebase()

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("notifier", None)

    def test_second_concurrent_attempt_cannot_claim_same_event(self):
        state_ref = FakeStateRef()

        first = self.notifier._claim_event_shock(
            FakeTransaction(),
            state_ref,
            "event-1",
            "2026-08-01T07:00:00+09:00",
        )
        second = self.notifier._claim_event_shock(
            FakeTransaction(),
            state_ref,
            "event-1",
            "2026-08-01T07:00:00+09:00",
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(state_ref.state["status"], "claimed")

    def test_zero_delivery_release_allows_retry(self):
        state_ref = FakeStateRef()
        self.notifier._claim_event_shock(
            FakeTransaction(),
            state_ref,
            "event-1",
            "2026-08-01T07:00:00+09:00",
        )

        released = self.notifier._release_event_shock_claim(
            FakeTransaction(),
            state_ref,
        )
        retried = self.notifier._claim_event_shock(
            FakeTransaction(),
            state_ref,
            "event-1",
            "2026-08-01T07:00:00+09:00",
        )

        self.assertTrue(released)
        self.assertTrue(retried)

    def test_delivered_event_cannot_be_released_or_reclaimed(self):
        state_ref = FakeStateRef({"sent": True, "status": "delivered"})

        released = self.notifier._release_event_shock_claim(
            FakeTransaction(),
            state_ref,
        )
        claimed = self.notifier._claim_event_shock(
            FakeTransaction(),
            state_ref,
            "event-1",
            "2026-08-01T07:00:00+09:00",
        )

        self.assertFalse(released)
        self.assertFalse(claimed)


if __name__ == "__main__":
    unittest.main()
