import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import official_policy as policy
import build_macro_policy_signal as builder

NOW = datetime.fromisoformat("2026-10-28T16:00:00+09:00")
URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
CALENDAR = f'<h4>2026 FOMC Meetings</h4>September 15-16* <a href="{URL}">HTML</a>October 27-28 December 8-9*<h4>2025 FOMC Meetings</h4>'
STATEMENT = '<h2>September 16, 2026</h2><h3>Federal Reserve issues FOMC statement</h3><p>For release at 2:00 p.m. EDT</p><p>The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent.</p>'


def fetcher():
    return Mock(side_effect=lambda url: CALENDAR if url == policy.CALENDAR_URL else STATEMENT)


class OfficialPolicyTests(unittest.TestCase):
    def test_actual_decision_with_verified_release_time_and_no_forecast(self):
        fetch = fetcher()
        doc = policy.refresh_official_policy(now=NOW, fetcher=fetch)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(doc["quality_gate"]["status"], "passed")
        self.assertEqual(doc["sector"]["target_range_lower"], 3.75)
        self.assertEqual(doc["sector"]["target_range_upper"], 4)
        self.assertEqual(doc["sector"]["direction"], "hike")
        self.assertIsNone(doc["sector"]["probability_pct"])
        self.assertEqual(doc["sector"]["source_published_at"], "2026-09-17T03:00:00+09:00")
        self.assertEqual(doc["next_decision_date"], "2026-10-28")
        self.assertEqual(doc["next_check_at"], (NOW + timedelta(hours=1)).isoformat())
        self.assertNotEqual(doc["sector"]["source_published_at"], doc["last_checked_at"])

    def test_hold_and_cut_wording_and_decimal_ranges(self):
        for action, direction, phrase in [("maintain", "hold", "at 3.75 to 4"), ("lower", "cut", "by 1/4 percentage point to 3-1/2 to 3-3/4")]:
            body = STATEMENT.replace("raise", action).replace("by 1/4 percentage point to 3-3/4 to 4", phrase)
            result = policy.parse_statement(body, datetime(2026, 9, 16).date(), NOW)
            self.assertEqual(result[3], direction)

    def test_future_links_and_foreign_links_not_used(self):
        extra = '<a href="https://evil.example/newsevents/pressreleases/monetary20261027a.htm">HTML</a><a href="/newsevents/pressreleases/monetary20261209a.htm">HTML</a>'
        day, url, _ = policy.latest_statement(CALENDAR + extra, NOW)
        self.assertEqual(day.isoformat(), "2026-09-16")
        self.assertEqual(url, URL)

    def test_minutes_publication_does_not_supersede_the_policy_decision(self):
        calendar = CALENDAR.replace("October 27-28", "(Released October 07, 2026) October 27-28")
        day, url, next_day = policy.latest_statement(calendar, NOW)
        self.assertEqual(next_day.isoformat(), "2026-10-28")
        doc = policy.refresh_official_policy(now=NOW, fetcher=Mock(side_effect=lambda address: calendar if address == policy.CALENDAR_URL else STATEMENT))
        self.assertEqual(doc["quality_gate"]["status"], "passed")

    def test_publication_mismatch_future_clock_missing_clock_and_ambiguous_range_rejected(self):
        bodies = [STATEMENT.replace("September 16", "September 15"), STATEMENT.replace("EDT", "EST"), STATEMENT.replace("For release at 2:00 p.m. EDT", ""), STATEMENT + " target range for the federal funds rate at 1 to 2 percent", "<html>empty</html>"]
        for body in bodies:
            with self.subTest(body_length=len(body)), self.assertRaises(ValueError):
                policy.parse_statement(body, datetime(2026, 9, 16).date(), NOW)
        with self.assertRaises(ValueError):
            policy.parse_statement(STATEMENT, datetime(2026, 9, 16).date(), datetime.fromisoformat("2026-09-16T12:00:00-04:00"))

    def test_six_hour_cache_and_one_hour_meeting_day_cache(self):
        early = datetime.fromisoformat("2026-10-26T16:00:00+09:00")
        old = policy.refresh_official_policy(now=early, fetcher=fetcher())
        no_fetch = Mock(side_effect=AssertionError("unexpected network"))
        self.assertEqual(policy.refresh_official_policy(old, early + timedelta(hours=5), no_fetch), old)
        no_fetch.assert_not_called()
        fresh = policy.refresh_official_policy(old, early + timedelta(hours=6), fetcher())
        self.assertEqual(fresh["sector"]["source_published_at"], old["sector"]["source_published_at"])
        self.assertNotEqual(fresh["last_success_at"], old["last_success_at"])

    def test_timeout_uses_last_verified_without_extending_expiry_and_retry_one_hour(self):
        old = policy.refresh_official_policy(now=NOW, fetcher=fetcher())
        failed_at = NOW + timedelta(hours=1)
        failed = policy.refresh_official_policy(old, failed_at, Mock(side_effect=TimeoutError("unlogged body")))
        self.assertEqual(failed["data_status"], "last_verified")
        self.assertEqual(failed["verification_status"], "failed")
        self.assertEqual(failed["last_success_at"], old["last_success_at"])
        self.assertEqual(failed["expires_at"], old["expires_at"])
        self.assertEqual(failed["sector"]["source_published_at"], old["sector"]["source_published_at"])
        self.assertNotIn("unlogged body", json.dumps(failed))
        no_fetch = Mock()
        policy.refresh_official_policy(failed, failed_at + timedelta(minutes=30), no_fetch)
        no_fetch.assert_not_called()
        recovered = policy.refresh_official_policy(failed, failed_at + timedelta(hours=1), fetcher())
        self.assertEqual(recovered["verification_status"], "success")

    def test_empty_cache_provider_error_empty_data_and_expired_cache_are_unavailable(self):
        cases = [Mock(side_effect=TimeoutError()), Mock(side_effect=HTTPError(URL, 503, "unlogged", None, None)), Mock(return_value="<html>empty</html>")]
        for fetch in cases:
            doc = policy.refresh_official_policy(now=NOW, fetcher=fetch)
            self.assertEqual(doc["quality_gate"]["status"], "unavailable")
            self.assertIsNone(doc["signal_id"])
            self.assertNotIn("last_success_at", doc)
            self.assertEqual(doc["verification_status"], "failed")
        old = policy.refresh_official_policy(now=NOW, fetcher=fetcher())
        late = NOW + timedelta(days=2)
        failed = policy.refresh_official_policy(old, late, Mock(side_effect=TimeoutError()))
        self.assertEqual(failed["quality_gate"]["status"], "unavailable")
        self.assertIsNone(failed["signal_id"])

    def test_unavailable_failure_cache_does_not_refetch_half_hour(self):
        failed = policy.refresh_official_policy(now=NOW, fetcher=Mock(side_effect=TimeoutError()))
        fetch = Mock()
        again = policy.refresh_official_policy(failed, NOW + timedelta(minutes=30), fetch)
        fetch.assert_not_called()
        self.assertEqual(again, failed)

    def test_bounded_download_and_redirect_validation(self):
        for final, payload in [("https://evil.example/page", b"x"), (URL, b"x" * (policy.MAX_BYTES + 1))]:
            response = Mock()
            response.geturl.return_value = final
            response.read.return_value = payload
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            with patch.object(policy, "urlopen", return_value=response), self.assertRaises(ValueError):
                policy.fetch_official(URL)
        with self.assertRaises(ValueError):
            policy.fetch_official("https://evil.example/")

    def test_builder_shares_official_source_independent_of_event_results(self):
        doc = builder.build(NOW, refresh_official=True, fetcher=fetcher())
        self.assertEqual(doc["quality_gate"]["status"], "passed")
        self.assertEqual(doc["quality_gate"]["mode"], "official-policy-decision")
        self.assertEqual(doc["sector"]["target_range_lower"], 3.75)
        self.assertEqual(doc["last_success_at"], NOW.isoformat())
        unchanged = builder.build(NOW + timedelta(minutes=30), refresh_official=True, previous=doc, fetcher=Mock(side_effect=AssertionError()))
        self.assertFalse(builder.policy_changed(doc, unchanged))


if __name__ == "__main__":
    unittest.main()
