import json
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.schedule_audit import audit_schedule, evaluate_overdue, expected_slots, market_expectation, parse_time


START = parse_time("2026-10-24T00:00:00Z")
END = parse_time("2026-10-25T00:00:00Z")


def run(run_id, at, event="schedule", conclusion="success", status="completed", attempt=1):
    at = parse_time(at)
    return {
        "id": run_id, "created_at": at.isoformat(),
        "run_started_at": (at + timedelta(seconds=30)).isoformat(),
        "updated_at": (at + timedelta(minutes=3)).isoformat(),
        "event": event, "status": status, "conclusion": conclusion, "run_attempt": attempt,
    }


class ScheduleAuditTests(unittest.TestCase):
    def test_48_daily_slots_including_weekends_and_dst_days(self):
        for day in ("2026-10-24", "2026-03-08", "2026-11-01"):
            start = parse_time(day + "T00:00:00Z")
            slots = expected_slots(start, start + timedelta(days=1))
            self.assertEqual(len(slots), 48)
            self.assertEqual({slot.minute for slot in slots}, {0, 30})
            self.assertTrue(all(b - a == timedelta(minutes=30) for a, b in zip(slots, slots[1:])))

    def test_complete_day_has_no_missing_and_timeout_is_below_interval(self):
        runs = [run(i, slot) for i, slot in enumerate(expected_slots(START, END), 1)]
        result = audit_schedule(runs, START, END, END)
        coverage = result["expected_event_coverage"]
        self.assertEqual(coverage["expected_slots"], 48)
        self.assertEqual(coverage["matched_slots"], 48)
        self.assertEqual(coverage["missing_slot_candidates"], [])
        self.assertTrue(result["policy"]["timeout_shorter_than_interval"])
        self.assertFalse(result["collection"]["all_sources_complete_verified"])

    def test_gap_duplicate_and_api_row_duplication_are_separate(self):
        runs = [run(i, slot) for i, slot in enumerate(expected_slots(START, END), 1)]
        runs.pop(2)
        runs += [run(99, "2026-10-24T00:08:00Z"), dict(runs[0])]
        coverage = audit_schedule(runs, START, END)["expected_event_coverage"]
        self.assertEqual(coverage["missing_slot_candidates"], ["2026-10-24T01:00:00Z"])
        self.assertEqual(coverage["duplicate_slot_candidates"], [{
            "slot": "2026-10-24T00:00:00Z", "run_ids": [1, 99]}])
        self.assertTrue(coverage["matching_is_estimate"])

    def test_manual_or_marker_run_does_not_mask_missing_cron(self):
        rows = [run(1, "2026-10-24T00:07:00Z", "workflow_dispatch"),
                run(2, "2026-10-24T00:37:00Z", "push")]
        result = audit_schedule(rows, START, START + timedelta(hours=1))
        self.assertEqual(result["expected_event_coverage"]["matched_slots"], 0)
        self.assertEqual(result["latest_attempt"]["id"], 2)
        self.assertIsNone(result["latest_scheduled_attempt"])
        self.assertIsNone(result["minutes_since_latest_scheduled_attempt"])
        self.assertIsNone(result["collection"]["last_collection_success_at"])

    def test_attempt_process_success_and_collection_success_have_distinct_times(self):
        rows = [run(1, "2026-10-24T00:07:00Z"),
                run(2, "2026-10-24T00:37:00Z", conclusion="failure")]
        result = audit_schedule(rows, START, END, collection_metadata={
            "data": {"last_attempt_at": "2026-10-24T00:38:00Z", "collection_status": "partial"},
            "last_good": {"collection_finished_at": "2026-10-23T23:55:00Z", "collection_status": "success"},
        })
        self.assertEqual(result["latest_attempt"]["id"], 2)
        self.assertEqual(result["latest_process_success"]["id"], 1)
        self.assertEqual(result["collection"]["last_attempt_at"], "2026-10-24T00:38:00Z")
        self.assertEqual(result["collection"]["last_collection_success_at"], "2026-10-23T23:55:00Z")
        self.assertEqual(result["collection"]["collection_status"], "partial")
        self.assertFalse(result["collection"]["all_sources_complete_verified"])

    def test_cancel_timeout_rerun_and_lag(self):
        rows = [run(1, "2026-10-24T00:07:00Z", conclusion="cancelled"),
                run(2, "2026-10-24T00:37:00Z", conclusion="timed_out"),
                run(2, "2026-10-24T00:37:00Z", attempt=2)]
        rows[-1]["run_started_at"] = "2026-10-24T01:10:00Z"
        rows[-1]["updated_at"] = "2026-10-24T01:12:00Z"
        result = audit_schedule(rows, START, END)
        self.assertEqual(len(result["run_health"]["cancelled"]), 1)
        self.assertEqual(len(result["run_health"]["timed_out"]), 1)
        self.assertEqual(result["latest_attempt"]["run_attempt"], 2)
        self.assertEqual(result["latest_attempt"]["attempt_at"], "2026-10-24T01:10:00Z")
        self.assertEqual(result["expected_event_coverage"]["duplicate_slot_candidates"], [])

    def test_grace_and_delayed_unmatched_receipt_do_not_claim_precise_failure(self):
        now = parse_time("2026-10-24T00:14:59Z")
        result = audit_schedule([], START, now, now)
        self.assertEqual(result["expected_event_coverage"]["due_slots"], 0)
        delayed = audit_schedule([run(1, "2026-10-24T00:26:00Z")], START,
                                 START + timedelta(hours=1))
        self.assertEqual(len(delayed["expected_event_coverage"]["unmatched_expected_event_runs"]), 1)
        self.assertTrue(delayed["expected_event_coverage"]["matching_is_estimate"])

    def test_utc_schedule_and_us_session_dst_are_independent(self):
        summer = market_expectation("2026-07-06T13:30:00Z")
        winter_before = market_expectation("2026-01-05T13:30:00Z")
        winter_open = market_expectation("2026-01-05T14:30:00Z")
        self.assertTrue(summer["us"]["weekday_regular_session"])
        self.assertFalse(winter_before["us"]["weekday_regular_session"])
        self.assertTrue(winter_open["us"]["weekday_regular_session"])
        self.assertTrue(winter_before["collection_expected"])
        self.assertFalse(summer["holiday_calendar_complete"])

    def test_kr_hours_weekends_and_supplied_holiday(self):
        self.assertTrue(market_expectation("2026-10-26T00:00:00Z")["kr"]["weekday_regular_session"])
        self.assertFalse(market_expectation("2026-10-26T06:30:00Z")["kr"]["weekday_regular_session"])
        weekend = market_expectation("2026-10-24T14:00:00Z")
        self.assertFalse(weekend["us"]["weekday_regular_session"])
        self.assertTrue(weekend["collection_expected"])
        holiday = market_expectation("2026-12-25T15:00:00Z", {"us": ["2026-12-25"]})
        self.assertTrue(holiday["us"]["explicit_holiday"])
        self.assertFalse(holiday["us"]["weekday_regular_session"])

    def test_invalid_times_rejected_and_nonpublic_fields_not_emitted(self):
        with self.assertRaises(ValueError):
            parse_time("2026-10-24T00:00:00")
        with self.assertRaises(ValueError):
            expected_slots(END, START)
        row = run(1, "2026-10-24T00:07:00Z")
        row.update({"head_commit": {"message": "ignored-private-marker"}, "actor": {"login": "ignored-actor"}})
        result = audit_schedule([row, {"created_at": "invalid"}], START, END)
        self.assertEqual(result["run_health"]["invalid_metadata_rows"], 1)
        self.assertNotIn("ignored-private-marker", json.dumps(result))
        self.assertNotIn("ignored-actor", json.dumps(result))

    def test_workflow_contract_has_external_wrapper_and_suppressed_marker(self):
        root = Path(__file__).parent
        workflow = (root / ".github/workflows/market-data.yml").read_text()
        self.assertTrue((root / ".github/workflows/check_signals.yml").exists())
        self.assertNotIn("  schedule:", workflow)
        self.assertIn("  workflow_call:", workflow)
        self.assertIn("timeout-minutes: 25", workflow)
        self.assertIn(".github/refresh-market-data.json", workflow)
        self.assertIn("type: boolean", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("github.event_name == 'push' || inputs.suppress_push == true", workflow)
        self.assertIn('SAYO_DISABLE_PUSH:', workflow)
        self.assertNotIn("'${{ secrets.", workflow)
        wrapper = (root / ".github/workflows/check_signals.yml").read_text()
        self.assertIn("  workflow_dispatch:", wrapper)
        self.assertIn("uses: ./.github/workflows/market-data.yml", wrapper)
        self.assertIn("secrets: inherit", wrapper)
        self.assertNotIn("concurrency:", wrapper)
        self.assertNotIn("  schedule:", wrapper)
        self.assertIn("--expected-event workflow_dispatch --slot-offset-minutes 0", workflow)

    def test_external_dispatch_target_slots_are_generic_and_configurable(self):
        rows = [run(1, "2026-10-24T00:00:00Z", event="workflow_dispatch"),
                run(2, "2026-10-24T00:30:00Z", event="workflow_dispatch"),
                run(3, "2026-10-24T00:30:00Z", event="push")]
        result = audit_schedule(rows, START, START + timedelta(hours=1),
                                expected_event="workflow_dispatch")
        self.assertEqual(result["expected_event_coverage"]["matched_slots"], 2)
        self.assertEqual(result["expected_event_coverage"]["duplicate_slot_candidates"], [])
        self.assertEqual(result["policy"]["expected_slot_minutes_utc"], [0, 30])
        self.assertIn("external scheduler configuration unverified", result["policy"]["slot_basis"])
        self.assertNotIn("cron_utc", result["policy"])
        offset = expected_slots(START, START + timedelta(hours=1), offset_minutes=7)
        self.assertEqual([slot.minute for slot in offset], [7, 37])
        with self.assertRaises(ValueError):
            expected_slots(START, END, offset_minutes=30)


class ScheduleHealthTests(unittest.TestCase):
    activated = "2026-10-24T00:00:00Z"

    def report(self, rows=(), now="2026-10-24T02:00:00Z"):
        return audit_schedule(list(rows), START, now, now)

    def test_missing_collector_fails_after_warmup_without_any_collector_run(self):
        health = evaluate_overdue(self.report(), self.activated)
        self.assertEqual(health["status"], "overdue")
        self.assertEqual(health["reasons"], ["no_expected_event_since_activation", "no_process_success_since_activation"])

    def test_initial_80_minute_warmup(self):
        report = self.report(now="2026-10-24T01:19:59Z")
        health = evaluate_overdue(report, self.activated)
        self.assertEqual(health["status"], "warmup")
        self.assertFalse(health["overdue"])
        health = evaluate_overdue(self.report(now="2026-10-24T01:20:00Z"), self.activated)
        self.assertTrue(health["overdue"])
        self.assertEqual(health["reasons"], ["no_expected_event_since_activation"])

    def test_one_old_missing_slot_does_not_fail_latest_health(self):
        report = self.report([run(1, "2026-10-24T01:37:00Z")])
        self.assertTrue(report["expected_event_coverage"]["missing_slot_candidates"])
        health = evaluate_overdue(report, self.activated)
        self.assertEqual(health["status"], "healthy")
        self.assertFalse(health["platform_independent"])
        self.assertEqual(health["data_freshness"], "separate_audit_required")

    def test_recent_manual_and_marker_success_cannot_hide_old_schedule_receipt(self):
        rows = [run(1, "2026-10-24T00:37:00Z"),
                run(2, "2026-10-24T01:45:00Z", event="workflow_dispatch"),
                run(3, "2026-10-24T01:50:00Z", event="push")]
        report = self.report(rows)
        self.assertEqual(report["latest_attempt"]["id"], 3)
        self.assertEqual(report["latest_scheduled_attempt"]["id"], 1)
        self.assertEqual(report["minutes_since_latest_scheduled_attempt"], 83)
        health = evaluate_overdue(report, self.activated)
        self.assertEqual(health["expected_event"], "schedule")
        self.assertEqual(health["reasons"], ["latest_expected_event_overdue"])
        self.assertEqual(health["minutes_since_latest_expected_event"], 83)
        self.assertEqual(health["minutes_since_latest_process_success"], 7)

    def test_no_cron_with_recent_manual_success_is_still_scheduler_failure(self):
        report = self.report([run(1, "2026-10-24T01:50:00Z", event="workflow_dispatch")])
        health = evaluate_overdue(report, self.activated)
        self.assertEqual(health["reasons"], ["no_expected_event_since_activation"])
        self.assertTrue(health["overdue"])

    def test_manual_rerun_of_schedule_cannot_refresh_original_cron_receipt(self):
        rerun = run(1, "2026-10-24T00:07:00Z", attempt=2)
        rerun["run_started_at"] = "2026-10-24T01:50:00Z"
        rerun["updated_at"] = "2026-10-24T01:53:00Z"
        report = self.report([rerun])
        self.assertEqual(report["minutes_since_latest_attempt"], 10)
        self.assertEqual(report["minutes_since_latest_scheduled_attempt"], 113)
        health = evaluate_overdue(report, self.activated)
        self.assertEqual(health["minutes_since_latest_expected_event"], 113)
        self.assertEqual(health["reasons"], ["latest_expected_event_overdue"])

    def test_alternate_expected_event_requires_explicit_choice(self):
        report = self.report([run(1, "2026-10-24T01:50:00Z", event="workflow_dispatch")])
        self.assertTrue(evaluate_overdue(report, self.activated)["overdue"])
        dispatched = evaluate_overdue(report, self.activated, expected_event="workflow_dispatch")
        self.assertEqual(dispatched["status"], "healthy")
        any_event = evaluate_overdue(report, self.activated, expected_event="any")
        self.assertEqual(any_event["status"], "healthy")
        with self.assertRaises(ValueError):
            evaluate_overdue(report, self.activated, expected_event="push")

    def test_attempt_fresh_but_old_process_success_fails_success_only(self):
        rows = [run(1, "2026-10-24T00:07:00Z"),
                run(2, "2026-10-24T01:50:00Z", status="in_progress", conclusion=None)]
        health = evaluate_overdue(self.report(rows), self.activated)
        self.assertEqual(health["reasons"], ["latest_process_success_overdue"])

    def test_exact_threshold_is_allowed_then_fails_after(self):
        rows = [run(1, "2026-10-24T00:27:00Z"),
                run(2, "2026-10-24T00:55:00Z", status="in_progress", conclusion=None)]
        health = evaluate_overdue(self.report(rows), self.activated)
        self.assertFalse(health["overdue"])
        later = evaluate_overdue(self.report(rows, now="2026-10-24T02:00:01Z"), self.activated)
        self.assertEqual(later["reasons"], ["latest_expected_event_overdue", "latest_process_success_overdue"])

    def test_invalid_activation_is_failure_instead_of_permanent_warmup(self):
        for activated in (None, "invalid", "2026-10-25T00:00:00Z"):
            with self.subTest(activated=activated):
                health = evaluate_overdue(self.report(), activated)
                self.assertEqual(health["status"], "invalid_configuration")
                self.assertTrue(health["overdue"])

    def test_prior_workflow_history_does_not_establish_new_path_coverage(self):
        row = run(1, "2026-10-23T23:37:00Z")
        health = evaluate_overdue(self.report([row]), self.activated)
        self.assertEqual(health["reasons"], ["no_expected_event_since_activation", "no_process_success_since_activation"])

    def test_cli_fail_on_overdue_emits_report_then_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "public-runs.json"
            marker = Path(directory) / "public-marker.json"
            fixture.write_text(json.dumps({"workflow_runs": []}))
            marker.write_text(json.dumps({"monitoring_started_at": self.activated}))
            command = [sys.executable, "-B", "scripts/schedule_audit.py", "--runs", str(fixture),
                       "--activation-marker", str(marker), "--now", "2026-10-24T02:00:00Z"]
            read_only = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(read_only.returncode, 0)
            failing = subprocess.run(command + ["--fail-on-overdue"], capture_output=True, text=True, check=False)
            self.assertEqual(failing.returncode, 1)
            self.assertEqual(json.loads(failing.stdout)["schedule_health"]["status"], "overdue")

    def test_cli_expected_event_is_schedule_by_default_and_dispatch_is_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "public-runs.json"
            marker = Path(directory) / "public-marker.json"
            fixture.write_text(json.dumps({"workflow_runs": [
                run(1, "2026-10-24T01:50:00Z", event="workflow_dispatch")]}))
            marker.write_text(json.dumps({"monitoring_started_at": self.activated}))
            command = [sys.executable, "-B", "scripts/schedule_audit.py", "--runs", str(fixture),
                       "--activation-marker", str(marker), "--now", "2026-10-24T02:00:00Z", "--fail-on-overdue"]
            default = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(default.returncode, 1)
            alternate = subprocess.run(command + ["--expected-event", "workflow_dispatch"],
                                       capture_output=True, text=True, check=False)
            self.assertEqual(alternate.returncode, 0)
            self.assertEqual(json.loads(alternate.stdout)["schedule_health"]["expected_event"], "workflow_dispatch")

    def test_monitor_is_read_only_and_cannot_dispatch_a_collector(self):
        workflow = Path(".github/workflows/schedule-health.yml").read_text()
        self.assertIn("cron: '25 * * * *'", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("--fail-on-overdue", workflow)
        self.assertIn("--expected-event workflow_dispatch", workflow)
        self.assertIn("actions/workflows/check_signals.yml/runs", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("uploader.py", workflow)
        self.assertNotIn("notifier.py", workflow)
        self.assertNotIn("--method POST", workflow)
        self.assertNotIn("/dispatches", workflow)
        self.assertIn("--active-market-hours", workflow)


class ActiveMarketHoursTests(unittest.TestCase):
    activated = "2026-10-23T00:00:00Z"

    def health(self, now, rows=(), holidays=None):
        report = audit_schedule(list(rows), "2026-10-23T00:00:00Z", now, now,
                                expected_event="workflow_dispatch", active_market_hours=True,
                                holidays=holidays)
        return report, evaluate_overdue(report, self.activated,
                                       expected_event="workflow_dispatch", active_market_hours=True,
                                       holidays=holidays)

    def test_saturday_has_no_expected_slots_and_no_overdue_failure(self):
        slots = expected_slots(START, END, active_market_hours=True)
        self.assertEqual(slots, [])
        report, health = self.health("2026-10-24T14:00:00Z")
        saturday_report = audit_schedule([], START, END, "2026-10-24T14:00:00Z",
                                         active_market_hours=True, expected_event="workflow_dispatch")
        self.assertEqual(saturday_report["expected_event_coverage"]["expected_slots"], 0)
        self.assertEqual(health["status"], "outside_session")
        self.assertFalse(health["overdue"])
        self.assertFalse(report["market_expectation"]["collection_expected"])

    def test_weekday_denominator_uses_two_regular_sessions_only(self):
        slots = expected_slots("2026-10-26T00:00:00Z", "2026-10-27T00:00:00Z",
                               active_market_hours=True)
        self.assertEqual(len(slots), 28)
        self.assertIn(parse_time("2026-10-26T00:00:00Z"), slots)  # KRX 09:00
        self.assertIn(parse_time("2026-10-26T06:00:00Z"), slots)  # KRX 15:00
        self.assertIn(parse_time("2026-10-26T06:30:00Z"), slots)  # KRX closing snapshot
        self.assertIn(parse_time("2026-10-26T13:30:00Z"), slots)  # NYSE 09:30 EDT
        self.assertIn(parse_time("2026-10-26T20:00:00Z"), slots)  # NYSE closing snapshot

    def test_slot_endpoints_include_closing_snapshot_but_overdue_stops_at_close(self):
        for before, opens, last, closes in (
            ("2026-10-26T08:59:59+09:00", "2026-10-26T09:00:00+09:00",
             "2026-10-26T15:29:59+09:00", "2026-10-26T15:30:00+09:00"),
            ("2026-10-26T09:29:59-04:00", "2026-10-26T09:30:00-04:00",
             "2026-10-26T15:59:59-04:00", "2026-10-26T16:00:00-04:00"),
        ):
            with self.subTest(opens=opens):
                self.assertFalse(market_expectation(before, active_market_hours=True)["collection_expected"])
                self.assertTrue(market_expectation(opens, active_market_hours=True)["collection_expected"])
                self.assertTrue(market_expectation(last, active_market_hours=True)["collection_expected"])
                self.assertTrue(market_expectation(closes, active_market_hours=True)["collection_expected"])
                self.assertFalse(any(market_expectation(closes)[market]["weekday_regular_session"]
                                     for market in ("kr", "us")))
                _, close_health = self.health(closes)
                self.assertEqual(close_health["status"], "outside_session")

    def test_monday_kr_open_ignores_weekend_gap_and_resets_both_graces(self):
        old = run(1, "2026-10-23T06:00:00Z", event="workflow_dispatch")
        for now, expected_age in (("2026-10-26T00:00:00Z", 0), ("2026-10-26T01:05:00Z", 65)):
            _, health = self.health(now, [old])
            self.assertEqual(health["status"], "session_grace")
            self.assertFalse(health["overdue"])
            self.assertEqual(health["minutes_since_latest_expected_event"], expected_age)
            self.assertEqual(health["minutes_since_latest_process_success"], expected_age)
            self.assertEqual(health["grace_origin_at"], "2026-10-26T00:00:00Z")
        _, late = self.health("2026-10-26T01:05:01Z", [old])
        self.assertEqual(late["reasons"], ["no_expected_event_since_session_open"])
        _, very_late = self.health("2026-10-26T01:30:01Z", [old])
        self.assertEqual(very_late["reasons"], ["no_expected_event_since_session_open", "no_process_success_since_session_open"])

    def test_monday_ny_open_does_not_carry_kr_session_or_weekend_gap(self):
        kr = run(1, "2026-10-26T06:00:00Z", event="workflow_dispatch")
        _, health = self.health("2026-10-26T13:30:00Z", [kr])
        self.assertEqual(health["status"], "session_grace")
        self.assertEqual(health["current_session"]["markets"], ["us"])
        self.assertEqual(health["grace_origin_at"], "2026-10-26T13:30:00Z")
        self.assertEqual(health["minutes_since_latest_expected_event"], 0)

    def test_current_session_receipt_and_any_success_have_separate_ages(self):
        external = run(1, "2026-10-26T00:30:00Z", event="workflow_dispatch")
        manual = run(2, "2026-10-26T01:30:00Z", event="push")
        _, health = self.health("2026-10-26T01:40:00Z", [external, manual])
        self.assertEqual(health["reasons"], ["latest_expected_event_overdue"])
        self.assertEqual(health["minutes_since_latest_expected_event"], 70)
        self.assertEqual(health["minutes_since_latest_process_success"], 7)

    def test_us_dst_spring_changes_utc_open_without_changing_local_hours(self):
        before = market_expectation("2026-03-06T13:30:00Z", active_market_hours=True)
        winter_open = market_expectation("2026-03-06T14:30:00Z", active_market_hours=True)
        after = market_expectation("2026-03-09T13:30:00Z", active_market_hours=True)
        self.assertFalse(before["us"]["weekday_regular_session"])
        self.assertTrue(winter_open["us"]["weekday_regular_session"])
        self.assertTrue(after["us"]["weekday_regular_session"])
        self.assertEqual(winter_open["us"]["opens_at"], "2026-03-06T14:30:00Z")
        self.assertEqual(after["us"]["opens_at"], "2026-03-09T13:30:00Z")

    def test_us_dst_autumn_changes_utc_open_and_session_grace(self):
        summer = market_expectation("2026-10-30T13:30:00Z", active_market_hours=True)
        before_open = market_expectation("2026-11-02T13:30:00Z", active_market_hours=True)
        winter = market_expectation("2026-11-02T14:30:00Z", active_market_hours=True)
        self.assertTrue(summer["us"]["weekday_regular_session"])
        self.assertFalse(before_open["us"]["weekday_regular_session"])
        self.assertTrue(winter["us"]["weekday_regular_session"])
        self.assertEqual(winter["us"]["closes_at"], "2026-11-02T21:00:00Z")
        _, health = self.health("2026-11-02T14:30:00Z")
        self.assertEqual(health["status"], "session_grace")
        self.assertEqual(health["minutes_since_latest_expected_event"], 0)

    def test_explicit_holidays_are_excluded_but_calendar_is_not_claimed_complete(self):
        holidays = {"kr": ["2026-10-26"], "us": ["2026-10-26"]}
        slots = expected_slots("2026-10-26T00:00:00Z", "2026-10-27T00:00:00Z",
                               active_market_hours=True, holidays=holidays)
        self.assertEqual(slots, [])
        report, health = self.health("2026-10-26T14:00:00Z", holidays=holidays)
        self.assertEqual(health["status"], "outside_session")
        self.assertFalse(health["overdue"])
        self.assertFalse(report["market_expectation"]["holiday_calendar_complete"])

    def test_cli_active_hours_weekend_exits_zero_with_outside_session(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "public-runs.json"
            marker = Path(directory) / "public-marker.json"
            fixture.write_text(json.dumps({"workflow_runs": []}))
            marker.write_text(json.dumps({"monitoring_started_at": self.activated}))
            command = [sys.executable, "-B", "scripts/schedule_audit.py", "--runs", str(fixture),
                       "--activation-marker", str(marker), "--now", "2026-10-24T14:00:00Z",
                       "--expected-event", "workflow_dispatch", "--active-market-hours", "--fail-on-overdue"]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["schedule_health"]["status"], "outside_session")


if __name__ == "__main__":
    unittest.main()
