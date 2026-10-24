#!/usr/bin/env python3
"""Audit supplied public Actions metadata; never load credentials or call an API.

GitHub exposes receipt time, not a reliable nominal cron slot for every run.
Slot matches and missing/duplicate candidates are therefore estimates, not proof
of scheduler failure. Manual and marker runs cannot fill scheduled coverage.
Collection metadata is optional: {"data": {...}, "last_good": {...}} containing
only public stocks/data and stocks/last_good. Process success never implies
that core collection, supplemental feeds, or all source observations are fresh.
Health defaults to original schedule receipts: a manual run, marker push, or
rerun cannot refresh the cron clock. --expected-event workflow_dispatch/any is
an explicit audit option for a separately approved change in scheduler ownership.
"""

import argparse
import json
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
INTERVAL = timedelta(minutes=30)
JOB_TIMEOUT_MINUTES = 25


def parse_time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timezone-aware timestamp required")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def optional_time(value):
    try:
        return parse_time(value)
    except (TypeError, ValueError, OverflowError):
        return None


def expected_slots(start, end, offset_minutes=0):
    """30-minute target slots in [start, end); offset is an observation assumption."""
    if not 0 <= offset_minutes < 30:
        raise ValueError("slot offset must be 0..29 minutes")
    start, end = parse_time(start), parse_time(end)
    if end < start:
        raise ValueError("end must not precede start")
    slot = start.replace(minute=offset_minutes, second=0, microsecond=0)
    while slot < start:
        slot += INTERVAL
    slots = []
    while slot < end:
        slots.append(slot)
        slot += INTERVAL
    return slots


def market_expectation(at, holidays=None):
    """Weekday regular-session expectation, with explicit optional holiday dates.

This is not an exchange calendar: unsupplied holidays/early closes stay unknown.
Collection is scheduled 24h regardless of whether either exchange is open.
"""
    at = parse_time(at)
    holidays = holidays or {}
    result = {"collection_expected": True, "holiday_calendar_complete": False}
    for market, zone, opens, closes in (
        ("us", "America/New_York", time(9, 30), time(16)),
        ("kr", "Asia/Seoul", time(9), time(15, 30)),
    ):
        local = at.astimezone(ZoneInfo(zone))
        holiday = local.date().isoformat() in set(holidays.get(market, []))
        result[market] = {
            "local_at": local.isoformat(),
            "weekday_regular_session": (
                local.weekday() < 5 and not holiday
                and opens <= local.time().replace(tzinfo=None) < closes
            ),
            "explicit_holiday": holiday,
            "basis": "weekday_hours_and_supplied_holidays; early_closes_unverified",
        }
    return result


def _public_runs(payload):
    rows = payload.get("workflow_runs", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("runs must be a list or a public workflow_runs response")
    unique, invalid = {}, 0
    for row in rows:
        if not isinstance(row, dict) or not (created := optional_time(row.get("created_at"))):
            invalid += 1
            continue
        run_id = row.get("id")
        if not isinstance(run_id, (str, int)) or isinstance(run_id, bool):
            invalid += 1
            continue
        attempt = row.get("run_attempt", 1)
        try:
            attempt = int(attempt)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if attempt < 1:
            invalid += 1
            continue
        record = {
            "id": run_id, "run_attempt": attempt,
            "event": row.get("event"), "status": row.get("status"),
            "conclusion": row.get("conclusion"), "created": created,
            "started": optional_time(row.get("run_started_at")),
            "updated": optional_time(row.get("updated_at")),
        }
        record["attempt_at"] = record["started"] if attempt > 1 and record["started"] else created
        unique[(str(run_id), attempt)] = record
    return sorted(unique.values(), key=lambda r: (r["created"], str(r["id"]), r["run_attempt"])), invalid


def _brief(run):
    if run is None:
        return None
    return {
        "id": run["id"], "run_attempt": run["run_attempt"],
        "event": run["event"], "status": run["status"],
        "conclusion": run["conclusion"], "received_at": iso(run["created"]),
        "attempt_at": iso(run["attempt_at"]),
        "run_started_at": iso(run["started"]), "updated_at": iso(run["updated"]),
    }


def _collection_summary(metadata):
    metadata = metadata if isinstance(metadata, dict) else {}
    data = metadata.get("data") or {}
    last_good = metadata.get("last_good") or {}
    if not isinstance(data, dict) or not isinstance(last_good, dict):
        raise ValueError("collection metadata data/last_good must be objects")
    successes = [optional_time(record.get("collection_finished_at"))
                 for record in (data, last_good) if record.get("collection_status") == "success"]
    successes = [value for value in successes if value]
    return {
        "last_attempt_at": iso(optional_time(data.get("last_attempt_at"))),
        "collection_status": data.get("collection_status", "unverified"),
        "last_collection_success_at": iso(max(successes)) if successes else None,
        "all_sources_complete_verified": False,
        "scope": "public core collection status only; supplemental feeds and observation freshness need separate audit",
    }


def audit_schedule(payload, start, end, now=None, grace_minutes=15, match_minutes=25,
                   collection_metadata=None, holidays=None, expected_event="schedule", slot_offset_minutes=0):
    start, end = parse_time(start), parse_time(end)
    now = parse_time(now or end)
    if not 0 <= grace_minutes < 60 or not 0 <= match_minutes < 30:
        raise ValueError("grace must be 0..59; slot match tolerance must be 0..29 minutes")
    if expected_event not in {"schedule", "workflow_dispatch", "any"}:
        raise ValueError("expected_event must be schedule, workflow_dispatch, or any")
    slots = expected_slots(start, end, slot_offset_minutes)
    runs, invalid = _public_runs(payload)
    # Keep history returned by API for latest success even if outside coverage window.
    visible = [r for r in runs if r["attempt_at"] <= now]
    latest = max(visible, key=lambda r: (r["attempt_at"], r["run_attempt"]), default=None)
    latest_by_event = {
        event: max((r for r in visible if r["event"] == event),
                   key=lambda r: r["created"], default=None)
        for event in ("schedule", "workflow_dispatch", "push")
    }
    latest_scheduled = latest_by_event["schedule"]
    successes = [r for r in visible if r["status"] == "completed" and r["conclusion"] == "success"]
    latest_success = max(successes, key=lambda r: r["updated"] or r["created"], default=None)
    coverage = {slot: [] for slot in slots}
    unmatched = []
    for run in visible:
        if (expected_event != "any" and run["event"] != expected_event) or run["run_attempt"] != 1:
            continue
        prior_slot = run["created"].replace(minute=slot_offset_minutes, second=0, microsecond=0)
        while prior_slot > run["created"]:
            prior_slot -= INTERVAL
        while prior_slot + INTERVAL <= run["created"]:
            prior_slot += INTERVAL
        if prior_slot in coverage and run["created"] - prior_slot <= timedelta(minutes=match_minutes):
            coverage[prior_slot].append(run)
        elif start <= run["created"] < end:
            unmatched.append(_brief(run))
    due = [slot for slot in slots if slot + timedelta(minutes=grace_minutes) <= now]
    missing = [iso(slot) for slot in due if not coverage[slot]]
    duplicates = [{"slot": iso(slot), "run_ids": [r["id"] for r in entries]}
                  for slot, entries in coverage.items() if len(entries) > 1]
    in_window = [r for r in visible if start <= r["attempt_at"] < end]
    startup_lags = [(r["started"] - r["created"]).total_seconds()
                    for r in in_window if r["run_attempt"] == 1
                    and r["started"] and r["started"] >= r["created"]]
    return {
        "schema_version": "1.0", "observed_at": iso(now),
        "window": {"start": iso(start), "end": iso(end)},
        "policy": {"expected_slot_minutes_utc": [slot_offset_minutes, slot_offset_minutes + 30],
                   "slot_basis": "target cadence / supplied observation offset; external scheduler configuration unverified",
                   "expected_event": expected_event, "daily_slots": 48,
                   "job_timeout_minutes": JOB_TIMEOUT_MINUTES,
                   "interval_minutes": 30, "timeout_shorter_than_interval": True,
                   "grace_minutes": grace_minutes, "slot_match_tolerance_minutes": match_minutes},
        "latest_attempt": _brief(latest),
        "latest_scheduled_attempt": _brief(latest_scheduled),
        "latest_attempts_by_event": {event: _brief(run) for event, run in latest_by_event.items()},
        "latest_process_success": _brief(latest_success),
        "minutes_since_latest_attempt": round((now - latest["attempt_at"]).total_seconds() / 60, 2) if latest else None,
        "minutes_since_latest_scheduled_attempt": round((now - latest_scheduled["created"]).total_seconds() / 60, 2) if latest_scheduled else None,
        "collection": _collection_summary(collection_metadata),
        "expected_event_coverage": {
            "expected_slots": len(slots), "due_slots": len(due),
            "matched_slots": sum(bool(coverage[slot]) for slot in due),
            "missing_slot_candidates": missing, "duplicate_slot_candidates": duplicates,
            "unmatched_expected_event_runs": unmatched,
            "matching_is_estimate": True,
        },
        "run_health": {
            "cancelled": [_brief(r) for r in in_window if r["conclusion"] == "cancelled"],
            "timed_out": [_brief(r) for r in in_window if r["conclusion"] == "timed_out"],
            "failed": [_brief(r) for r in in_window if r["conclusion"] == "failure"],
            "reruns": [_brief(r) for r in in_window if r["run_attempt"] > 1],
            "max_receipt_to_run_start_seconds": max(startup_lags, default=None),
            "invalid_metadata_rows": invalid,
        },
        "market_expectation": market_expectation(now, holidays),
        "limitations": [
            "GitHub schedule can be delayed or dropped; receipt-based missing/duplicate slots are candidates.",
            "API result may be paginated/truncated; all-time latest success and missing history can be unverified.",
            "run_started_at is public run metadata, not a measurement of the first collector instruction.",
            "Workflow success does not prove provider completeness or source freshness.",
            "workflow_dispatch does not identify the caller: manual wrapper runs and external cron cannot be distinguished.",
        ],
    }


def evaluate_overdue(report, monitoring_started_at, attempt_minutes=65,
                     success_minutes=90, warmup_minutes=80, expected_event="schedule"):
    """Check scheduler receipt gap and any process success as separate clocks.

    Default schedule health excludes manual/marker receipts and uses original
    created_at even for a rerun. Individual estimated old cron slots never fail
    this check. Alternate expected events do not change any collector schedule.
    """
    if expected_event not in {"schedule", "workflow_dispatch", "any"}:
        raise ValueError("expected_event must be schedule, workflow_dispatch, or any")
    now = parse_time(report["observed_at"])
    activated = optional_time(monitoring_started_at)
    result = {
        "status": "unverified", "overdue": False,
        "monitoring_started_at": iso(activated),
        "attempt_limit_minutes": attempt_minutes,
        "expected_event": expected_event,
        "process_success_limit_minutes": success_minutes,
        "warmup_minutes": warmup_minutes,
        "reasons": [],
        "platform_independent": False,
        "data_freshness": "separate_audit_required",
    }
    if min(attempt_minutes, success_minutes, warmup_minutes) < 0:
        raise ValueError("health thresholds must be nonnegative")
    if not activated or activated > now + timedelta(minutes=5):
        result.update(status="invalid_configuration", overdue=True,
                      reasons=["missing_or_future_monitoring_started_at"])
        return result
    elapsed = (now - activated).total_seconds() / 60
    result["minutes_since_activation"] = round(elapsed, 2)
    if elapsed < warmup_minutes:
        result.update(status="warmup", reasons=["initial_scheduler_warmup"])
        return result
    if expected_event == "any":
        latest = report.get("latest_attempt") or {}
    elif expected_event == "schedule":
        latest = report.get("latest_scheduled_attempt") or {}
    else:
        latest = (report.get("latest_attempts_by_event") or {}).get(expected_event) or {}
    success = report.get("latest_process_success") or {}
    attempt_at = optional_time(latest.get("attempt_at" if expected_event == "any" else "received_at"))
    success_at = optional_time(success.get("updated_at"))
    # Runs before this workflow transition do not establish new-path coverage.
    if attempt_at and attempt_at < activated:
        attempt_at = None
    if success_at and success_at < activated:
        success_at = None
    attempt_age = (now - (attempt_at or activated)).total_seconds() / 60
    success_age = (now - (success_at or activated)).total_seconds() / 60
    result.update(minutes_since_latest_expected_event=round(attempt_age, 2),
                  latest_expected_event_received_at=iso(attempt_at),
                  minutes_since_latest_process_success=round(success_age, 2))
    if attempt_age > attempt_minutes:
        result["reasons"].append("latest_expected_event_overdue" if attempt_at else "no_expected_event_since_activation")
    if success_age > success_minutes:
        result["reasons"].append("latest_process_success_overdue" if success_at else "no_process_success_since_activation")
    result["overdue"] = bool(result["reasons"])
    result["status"] = "overdue" if result["overdue"] else "healthy"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--collection-metadata", type=Path)
    parser.add_argument("--now", type=parse_time)
    parser.add_argument("--start", type=parse_time)
    parser.add_argument("--end", type=parse_time)
    parser.add_argument("--grace-minutes", type=int, default=15)
    parser.add_argument("--slot-offset-minutes", type=int, default=0,
                        help="0..29, assumed target cadence offset; not proof of external cron configuration")
    parser.add_argument("--activation-marker", type=Path,
                        help="read only monitoring_started_at from the public migration marker")
    parser.add_argument("--fail-on-overdue", action="store_true")
    parser.add_argument("--attempt-limit-minutes", type=int, default=65)
    parser.add_argument("--success-limit-minutes", type=int, default=90)
    parser.add_argument("--warmup-minutes", type=int, default=80)
    parser.add_argument("--expected-event", choices=("schedule", "workflow_dispatch", "any"),
                        default="schedule", help="scheduler receipt clock; manual/push cannot mask missing cron by default")
    args = parser.parse_args()
    now = args.now or datetime.now(UTC)
    end = args.end or now
    start = args.start or end - timedelta(days=1)
    metadata = json.loads(args.collection_metadata.read_text()) if args.collection_metadata else None
    result = audit_schedule(json.loads(args.runs.read_text()), start, end, now,
                            grace_minutes=args.grace_minutes, collection_metadata=metadata,
                            expected_event=args.expected_event, slot_offset_minutes=args.slot_offset_minutes)
    if args.fail_on_overdue or args.activation_marker:
        marker = json.loads(args.activation_marker.read_text()) if args.activation_marker else {}
        result["schedule_health"] = evaluate_overdue(
            result, marker.get("monitoring_started_at"), args.attempt_limit_minutes,
            args.success_limit_minutes, args.warmup_minutes, args.expected_event)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.fail_on_overdue and result["schedule_health"]["overdue"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
