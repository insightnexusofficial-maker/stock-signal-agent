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
            self.assertEqual({slot.minute for slot in slots}, {7, 37})
            self.assertTrue(all(b - a == timedelta(minutes=30) for a, b in zip(slots, slots[1:])))

    def test_complete_day_has_no_missing_and_timeout_is_below_interval(self):
        runs = [run(i, slot) for i, slot in enumerate(expected_slots(START, END), 1)]
        result = audit_schedule(runs, START, END, END)
        coverage = result["scheduled_coverage"]
        self.assertEqual(coverage["expected_slots"], 48)
        self.assertEqual(coverage["matched_slots"], 48)
        self.assertEqual(coverage["missing_slot_candidates"], [])
        self.assertTrue(result["policy"]["timeout_shorter_than_interval"])
        self.assertFalse(result["collection"]["all_sources_complete_verified"])

    def test_gap_duplicate_and_api_row_duplication_are_separate(self):
        runs = [run(i, slot) for i, slot in enumerate(expected_slots(START, END), 1)]
        runs.pop(2)
        runs += [run(99, "2026-10-24T00:08:00Z"), dict(runs[0])]
        coverage = audit_schedule(runs, START, END)["scheduled_coverage"]
        self.assertEqual(coverage["missing_slot_candidates"], ["2026-10-24T01:07:00Z"])
        self.assertEqual(coverage["duplicate_slot_candidates"], [{
            "slot": "2026-10-24T00:07:00Z", "run_ids": [1, 99]}])
        self.assertTrue(coverage["matching_is_estimate"])

    def test_manual_or_marker_run_does_not_mask_missing_cron(self):
        rows = [run(1, "2026-10-24T00:07:00Z", "workflow_dispatch"),
                run(2, "2026-10-24T00:37:00Z", "push")]
        result = audit_schedule(rows, START, START + timedelta(hours=1))
        self.assertEqual(result["scheduled_coverage"]["matched_slots"], 0)
        self.assertEqual(result["latest_attempt"]["id"], 2)
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
        self.assertEqual(result["scheduled_coverage"]["duplicate_slot_candidates"], [])

    def test_grace_and_delayed_unmatched_receipt_do_not_claim_precise_failure(self):
        now = parse_time("2026-10-24T00:15:00Z")
        result = audit_schedule([], START, now, now)
        self.assertEqual(result["scheduled_coverage"]["due_slots"], 0)
        delayed = audit_schedule([run(1, "2026-10-24T00:33:00Z")], START,
                                 START + timedelta(hours=1))
        self.assertEqual(len(delayed["scheduled_coverage"]["unmatched_scheduled_runs"]), 1)
        self.assertTrue(delayed["scheduled_coverage"]["matching_is_estimate"])

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

    def test_workflow_contract_has_single_schedule_and_suppressed_marker(self):
        root = Path(__file__).parent
        workflow = (root / ".github/workflows/market-data.yml").read_text()
        self.assertFalse((root / ".github/workflows/check_signals.yml").exists())
        self.assertIn("cron: '7,37 * * * *'", workflow)
        self.assertIn("timeout-minutes: 25", workflow)
        self.assertIn(".github/refresh-market-data.json", workflow)
        self.assertIn("type: boolean", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("github.event_name == 'push' || inputs.suppress_push == true", workflow)
        self.assertIn('SAYO_DISABLE_PUSH:', workflow)
        self.assertNotIn("'${{ secrets.", workflow)


class ScheduleHealthTests(unittest.TestCase):
    activated = "2026-10-24T00:00:00Z"

    def report(self, rows=(), now="2026-10-24T02:00:00Z"):
        return audit_schedule(list(rows), START, now, now)

    def test_missing_collector_fails_after_warmup_without_any_collector_run(self):
        health = evaluate_overdue(self.report(), self.activated)
        self.assertEqual(health["status"], "overdue")
        self.assertEqual(health["reasons"], ["no_attempt_since_activation", "no_process_success_since_activation"])

    def test_initial_80_minute_warmup(self):
        report = self.report(now="2026-10-24T01:19:59Z")
        health = evaluate_overdue(report, self.activated)
        self.assertEqual(health["status"], "warmup")
        self.assertFalse(health["overdue"])
        health = evaluate_overdue(self.report(now="2026-10-24T01:20:00Z"), self.activated)
        self.assertTrue(health["overdue"])
        self.assertEqual(health["reasons"], ["no_attempt_since_activation"])

    def test_one_old_missing_slot_does_not_fail_latest_health(self):
        report = self.report([run(1, "2026-10-24T01:37:00Z")])
        self.assertTrue(report["scheduled_coverage"]["missing_slot_candidates"])
        health = evaluate_overdue(report, self.activated)
        self.assertEqual(health["status"], "healthy")
        self.assertFalse(health["platform_independent"])
        self.assertEqual(health["data_freshness"], "separate_audit_required")

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
        self.assertEqual(later["reasons"], ["latest_attempt_overdue", "latest_process_success_overdue"])

    def test_invalid_activation_is_failure_instead_of_permanent_warmup(self):
        for activated in (None, "invalid", "2026-10-25T00:00:00Z"):
            with self.subTest(activated=activated):
                health = evaluate_overdue(self.report(), activated)
                self.assertEqual(health["status"], "invalid_configuration")
                self.assertTrue(health["overdue"])

    def test_prior_workflow_history_does_not_establish_new_path_coverage(self):
        row = run(1, "2026-10-23T23:37:00Z")
        health = evaluate_overdue(self.report([row]), self.activated)
        self.assertEqual(health["reasons"], ["no_attempt_since_activation", "no_process_success_since_activation"])

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

    def test_monitor_is_read_only_and_cannot_dispatch_a_collector(self):
        workflow = Path(".github/workflows/schedule-health.yml").read_text()
        self.assertIn("cron: '25 * * * *'", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("--fail-on-overdue", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("uploader.py", workflow)
        self.assertNotIn("notifier.py", workflow)
        self.assertNotIn("--method POST", workflow)
        self.assertNotIn("/dispatches", workflow)


if __name__ == "__main__":
    unittest.main()
