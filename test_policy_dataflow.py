"""No credentials or real network: Study -> official fallback -> public cache."""
import os
os.environ["SAYO_SKIP_FIREBASE_INIT"] = "1"

import copy
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

import uploader
from study.scripts import official_policy
from study.tests.test_official_policy import NOW, CALENDAR, STATEMENT, fetcher


class PolicyDataflowTests(unittest.TestCase):
    def official(self, previous=None, now=NOW):
        return official_policy.refresh_official_policy(previous, now, fetcher())

    def response(self, payload):
        response = Mock()
        response.json.return_value = payload
        return response

    def test_expired_study_recovers_directly_without_study_event_gate(self):
        with patch.object(uploader.requests, "get", return_value=self.response({"expires_at": "2026-09-17T00:00:00+09:00"})), patch.object(uploader, "_get_published_payload", return_value={}), patch.object(uploader, "refresh_official_policy", side_effect=self.official):
            signal = uploader.fetch_macro_policy_signal(now=NOW)
        self.assertEqual(signal["quality_status"], "passed")
        self.assertEqual(signal["target_range_lower"], 3.75)
        self.assertEqual(signal["verification_status"], "success")
        self.assertEqual(signal["study_sync_status"], "unavailable")
        self.assertIsNone(signal["probability_pct"])

    def test_flattened_public_cache_avoids_repeated_direct_fetch(self):
        signal = uploader._validated_macro_policy_signal(self.official(), NOW)
        no_fetch = Mock(side_effect=AssertionError("unexpected network"))
        cached = official_policy.refresh_official_policy(signal, NOW + timedelta(minutes=30), no_fetch)
        no_fetch.assert_not_called()
        accepted = uploader._validated_macro_policy_signal(cached, NOW + timedelta(minutes=30))
        self.assertEqual(accepted["source_published_at"], signal["source_published_at"])
        self.assertEqual(accepted["last_success_at"], signal["last_success_at"])

    def test_study_contract_and_official_source_observation_have_distinct_lifecycle(self):
        source = self.official()
        with patch.object(uploader.requests, "get", return_value=self.response(source)), patch.object(uploader, "_get_published_payload", return_value={}), patch.object(uploader, "refresh_official_policy", side_effect=self.official):
            signal = uploader.fetch_macro_policy_signal(now=NOW + timedelta(minutes=30))
        self.assertEqual(signal["study_sync_status"], "success")
        self.assertEqual(signal["source_published_at"], source["sector"]["source_published_at"])

    def test_timeout_preserves_valid_decision_and_marks_failed_verification(self):
        cached = uploader._validated_macro_policy_signal(self.official(), NOW)
        later = NOW + timedelta(hours=1)
        def fail_source(previous, now):
            return official_policy.refresh_official_policy(previous, now, Mock(side_effect=TimeoutError("raw body forbidden")))
        with patch.object(uploader.requests, "get", side_effect=TimeoutError()), patch.object(uploader, "_get_published_payload", return_value={"macro_policy_signal": cached}), patch.object(uploader, "refresh_official_policy", side_effect=fail_source):
            signal = uploader.fetch_macro_policy_signal(now=later)
        self.assertEqual(signal["quality_status"], "passed")
        self.assertEqual(signal["data_status"], "last_verified")
        self.assertEqual(signal["verification_status"], "failed")
        self.assertEqual(signal["last_success_at"], cached["last_success_at"])
        self.assertEqual(signal["expires_at"], cached["expires_at"])

    def test_empty_failure_is_explicit_unavailable_and_public_failure_cache_retries_hourly(self):
        def fail_source(previous, now):
            return official_policy.refresh_official_policy(previous, now, Mock(side_effect=TimeoutError()))
        with patch.object(uploader.requests, "get", side_effect=TimeoutError()), patch.object(uploader, "_get_published_payload", return_value={}), patch.object(uploader, "refresh_official_policy", side_effect=fail_source):
            signal = uploader.fetch_macro_policy_signal(now=NOW)
        self.assertEqual(signal["quality_status"], "unavailable")
        self.assertEqual(signal["verification_status"], "failed")
        no_fetch = Mock()
        again = official_policy.refresh_official_policy(signal, NOW + timedelta(minutes=30), no_fetch)
        no_fetch.assert_not_called()
        self.assertEqual(again["quality_gate"]["status"], "unavailable")

    def test_unverified_official_contract_rejected(self):
        source = self.official()
        for field, value in [("primary_source_url", "https://evil.example/policy"), ("source_published_at", "2027-01-01T12:00:00+09:00"), ("target_range_lower", float("nan")), ("probability_pct", 60)]:
            doc = copy.deepcopy(source)
            doc["sector"][field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                uploader._validated_macro_policy_signal(doc, NOW)


if __name__ == "__main__":
    unittest.main()
