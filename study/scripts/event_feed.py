"""공식 발표 일정과 검증 결과를 SAYO 공개 데이터로 정규화한다.

일정 파일은 공개 공식 출처만 담고, 실제 발표 결과는 별도 파일에서 관리한다.
이 모듈은 일정/결과를 매수 신호에 사용하지 않고 표시·동기화 상태만 만든다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CALENDAR_PATH = ROOT / "data" / "event-calendar.json"
DEFAULT_RESULTS_PATH = ROOT / "data" / "event-results.json"

SOURCE_SCHEMA_VERSION = "1.0"
PUBLIC_SCHEMA_VERSION = "1.2"
EVENT_KINDS = {"earnings", "company_metric", "macro"}
SCHEDULE_STATUSES = {"confirmed", "date_confirmed", "unconfirmed"}
RESULT_STATUSES = {"complete", "partial", "unavailable"}
REVIEW_STATUSES = {"verified", "pending", "rejected"}
TREND_CHANGES = {"strengthening", "unchanged", "weakening", "mixed"}
RISK_LEVELS = {"low", "medium", "high"}
CYCLE_STATUS_EFFECTS = {"none_single_source", "review_required", "evidence_candidate"}
AUTOMATED_OFFICIAL_RESULT_PREFIXES = ("macro-fomc-", "earnings-")


class EventDataError(ValueError):
    """공개 일정/결과 계약이 잘못됐을 때 사용한다."""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EventDataError(f"event data read failed: {path.name}") from error


def _parse_datetime(value, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError as error:
        raise EventDataError(f"invalid {field}") from error
    if parsed.tzinfo is None:
        raise EventDataError(f"{field} requires timezone")
    return parsed.astimezone(KST)


def _parse_date(value, field: str) -> date:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError as error:
        raise EventDataError(f"invalid {field}") from error


def _official_url(value, allowed_domains: set[str], field: str) -> str:
    url = str(value or "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise EventDataError(f"invalid {field}")
    if allowed_domains and not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        raise EventDataError(f"unapproved {field} domain")
    return url


def validate_calendar(document: dict) -> dict:
    if document.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise EventDataError("unsupported event calendar schema")

    allowed_domains = {
        str(domain).lower() for domain in document.get("allowed_domains", [])
        if str(domain).strip()
    }
    if not allowed_domains:
        raise EventDataError("allowed_domains is empty")

    shock_policy = document.get("shock_policy") or {}
    if shock_policy.get("notification_time_kst") != "07:00":
        raise EventDataError("shock notification time must be 07:00 KST")
    shock_rule_ids = set()
    for rule in shock_policy.get("rules", []):
        rule_id = str(rule.get("id") or "")
        if not rule_id or rule_id in shock_rule_ids:
            raise EventDataError("missing or duplicate shock rule id")
        shock_rule_ids.add(rule_id)
        if not str(rule.get("description") or "").strip():
            raise EventDataError(f"shock rule description missing: {rule_id}")
    if not shock_rule_ids:
        raise EventDataError("shock policy has no rules")

    generated_at = _parse_datetime(document.get("generated_at"), "generated_at")
    expires_at = _parse_datetime(document.get("expires_at"), "expires_at")
    if expires_at <= generated_at:
        raise EventDataError("calendar expires_at must follow generated_at")

    monitor_ids = set()
    earnings_tickers = set()
    for monitor in document.get("monitors", []):
        monitor_id = str(monitor.get("id") or "")
        if not monitor_id or monitor_id in monitor_ids:
            raise EventDataError("missing or duplicate monitor id")
        monitor_ids.add(monitor_id)
        if monitor.get("kind") == "earnings":
            ticker = str(monitor.get("ticker") or "").upper()
            if not ticker or ticker in earnings_tickers:
                raise EventDataError("missing or duplicate earnings ticker")
            earnings_tickers.add(ticker)
            if not str(monitor.get("primary_role") or "").strip():
                raise EventDataError(f"missing primary_role: {monitor_id}")
        _official_url(monitor.get("calendar_url"), allowed_domains, "calendar_url")
        if monitor.get("results_url"):
            _official_url(monitor.get("results_url"), allowed_domains, "results_url")

    event_ids = set()
    for event in document.get("events", []):
        event_id = str(event.get("id") or "")
        if not event_id or event_id in event_ids:
            raise EventDataError("missing or duplicate event id")
        event_ids.add(event_id)
        if event.get("kind") not in EVENT_KINDS:
            raise EventDataError(f"invalid event kind: {event_id}")
        if event.get("schedule_status") not in SCHEDULE_STATUSES:
            raise EventDataError(f"invalid schedule status: {event_id}")
        if not str(event.get("name") or "").strip():
            raise EventDataError(f"missing event name: {event_id}")
        _parse_date(event.get("scheduled_date"), f"{event_id}.scheduled_date")
        if event.get("scheduled_at"):
            _parse_datetime(event.get("scheduled_at"), f"{event_id}.scheduled_at")
        elif event.get("schedule_status") == "confirmed":
            raise EventDataError(f"confirmed event requires scheduled_at: {event_id}")
        monitor_after = _parse_datetime(event.get("monitor_after"), f"{event_id}.monitor_after")
        capture_until = _parse_datetime(event.get("capture_until"), f"{event_id}.capture_until")
        if capture_until <= monitor_after:
            raise EventDataError(f"capture window is reversed: {event_id}")
        _official_url(event.get("schedule_source_url"), allowed_domains, "schedule_source_url")
        if event.get("result_source_url"):
            _official_url(event.get("result_source_url"), allowed_domains, "result_source_url")

    return document


def validate_results(document: dict, calendar: dict) -> dict:
    if document.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise EventDataError("unsupported event result schema")
    _parse_datetime(document.get("generated_at"), "result generated_at")

    allowed_domains = {
        str(domain).lower() for domain in calendar.get("allowed_domains", [])
        if str(domain).strip()
    }
    event_ids = {event["id"] for event in calendar.get("events", [])}
    shock_rule_ids = {
        rule["id"] for rule in (calendar.get("shock_policy") or {}).get("rules", [])
    }
    result_ids = set()
    for result in document.get("results", []):
        event_id = str(result.get("event_id") or "")
        if not event_id or event_id not in event_ids or event_id in result_ids:
            raise EventDataError("unknown or duplicate result event_id")
        result_ids.add(event_id)
        if result.get("status") not in RESULT_STATUSES:
            raise EventDataError(f"invalid result status: {event_id}")
        if result.get("review_status") not in REVIEW_STATUSES:
            raise EventDataError(f"invalid review status: {event_id}")
        _parse_datetime(result.get("retrieved_at"), f"{event_id}.retrieved_at")
        if result.get("source_published_at"):
            _parse_datetime(result.get("source_published_at"), f"{event_id}.source_published_at")
        source_urls = result.get("source_urls") or []
        if not source_urls:
            raise EventDataError(f"result source missing: {event_id}")
        for source_url in source_urls:
            _official_url(source_url, allowed_domains, "result source")
        if result.get("status") == "complete" and result.get("review_status") != "verified":
            raise EventDataError(f"complete result is not verified: {event_id}")
        impact_review = result.get("impact_review")
        event = next(
            (item for item in calendar.get("events", []) if item.get("id") == event_id),
            None,
        )
        if result.get("status") == "complete" and event and event.get("kind") == "earnings":
            if not impact_review:
                raise EventDataError(f"complete earnings result has no impact review: {event_id}")
        if impact_review:
            if result.get("review_status") != "verified":
                raise EventDataError(f"unverified impact review: {event_id}")
            if impact_review.get("trend_change") not in TREND_CHANGES:
                raise EventDataError(f"invalid trend change: {event_id}")
            if impact_review.get("risk_level") not in RISK_LEVELS:
                raise EventDataError(f"invalid risk level: {event_id}")
            if impact_review.get("cycle_status_effect") not in CYCLE_STATUS_EFFECTS:
                raise EventDataError(f"invalid cycle status effect: {event_id}")
            if impact_review.get("audit_passed") is not True:
                raise EventDataError(f"impact review audit missing: {event_id}")
            _parse_datetime(
                impact_review.get("reviewed_at"),
                f"{event_id}.impact_review.reviewed_at",
            )
            for field in ("summary", "risk_summary"):
                if not str(impact_review.get(field) or "").strip():
                    raise EventDataError(f"impact review {field} missing: {event_id}")
        shock = result.get("shock") or {}
        if shock.get("is_shock") is True:
            if (
                result.get("status") != "complete"
                or result.get("review_status") != "verified"
                or shock.get("severity") != "shock"
                or shock.get("rule_id") not in shock_rule_ids
                or shock.get("audit_passed") is not True
                or not str(shock.get("reason") or "").strip()
            ):
                raise EventDataError(f"unverified shock result: {event_id}")

    return document


def _event_moment(event: dict) -> datetime:
    return _parse_datetime(event.get("scheduled_at") or event.get("monitor_after"), "event moment")


def _has_automated_official_collector(event: dict) -> bool:
    event_id = str(event.get("id") or "")
    return any(event_id.startswith(prefix) for prefix in AUTOMATED_OFFICIAL_RESULT_PREFIXES)


def _next_morning_seven(value: str) -> str:
    published = _parse_datetime(value, "shock published_at")
    next_day = published.date() + timedelta(days=1)
    return datetime.combine(next_day, time(hour=7), tzinfo=KST).isoformat(timespec="seconds")


def _public_result(result: dict, event: dict | None = None) -> dict:
    public = {
        "event_id": result["event_id"],
        "event_name": (event or {}).get("name"),
        "kind": (event or {}).get("kind"),
        "ticker": (event or {}).get("ticker"),
        "status": result["status"],
        "review_status": result["review_status"],
        "retrieved_at": _parse_datetime(
            result["retrieved_at"], "retrieved_at"
        ).isoformat(timespec="seconds"),
        "source_published_at": (
            _parse_datetime(
                result["source_published_at"], "source_published_at"
            ).isoformat(timespec="seconds")
            if result.get("source_published_at")
            else None
        ),
        "reference_period": result.get("reference_period"),
        "summary": result.get("summary"),
        "facts": result.get("facts") or [],
        "source_urls": result.get("source_urls") or [],
        "revision_of": result.get("revision_of"),
        "impact_review": result.get("impact_review"),
    }
    shock = result.get("shock") or {}
    if shock.get("is_shock") is True:
        public["shock"] = {
            "is_shock": True,
            "severity": "shock",
            "rule_id": shock["rule_id"],
            "reason": shock["reason"],
            "audit_passed": True,
            "notify_at": _next_morning_seven(
                result.get("source_published_at") or result["retrieved_at"]
            ),
        }
    return public


def build_event_sync(calendar_path=None, results_path=None, now=None) -> dict:
    """다가오는 일정, 수집 필요 항목, 최근 검증 결과를 공개용으로 만든다."""
    now = (now or datetime.now(KST)).astimezone(KST)
    calendar = validate_calendar(_read_json(Path(calendar_path or DEFAULT_CALENDAR_PATH)))
    results = validate_results(
        _read_json(Path(results_path or DEFAULT_RESULTS_PATH)),
        calendar,
    )
    result_by_event = {
        result["event_id"]: result for result in results.get("results", [])
    }
    event_by_id = {
        event["id"]: event for event in calendar.get("events", [])
    }
    earnings_events_by_ticker: dict[str, list[dict]] = {}
    for event in calendar.get("events", []):
        if event.get("kind") != "earnings" or not event.get("ticker"):
            continue
        earnings_events_by_ticker.setdefault(str(event["ticker"]).upper(), []).append(event)

    upcoming = []
    due_event_ids = []
    overdue_event_ids = []
    unsupported_due_event_ids = []
    lower_bound = now - timedelta(days=2)
    upper_bound = now + timedelta(days=45)
    for event in calendar.get("events", []):
        event_moment = _event_moment(event)
        capture_until = _parse_datetime(event["capture_until"], "capture_until")
        result = result_by_event.get(event["id"])
        verified_complete = bool(
            result
            and result.get("status") == "complete"
            and result.get("review_status") == "verified"
        )
        if verified_complete:
            sync_status = "synced"
        elif not event.get("scheduled_at"):
            # 시각은 추정하지 않되 공식 발표일 당일과 다음 날에는 공식 IR의
            # 결과 공개 여부를 확인한다.
            scheduled_date = _parse_date(event["scheduled_date"], "scheduled_date")
            if now.date() < scheduled_date:
                sync_status = "scheduled"
            elif now.date() <= scheduled_date + timedelta(days=1):
                sync_status = "due"
                due_event_ids.append(event["id"])
                if not _has_automated_official_collector(event):
                    unsupported_due_event_ids.append(event["id"])
            else:
                sync_status = "overdue"
                overdue_event_ids.append(event["id"])
        elif now < event_moment:
            sync_status = "scheduled"
        elif now <= capture_until:
            sync_status = "due"
            due_event_ids.append(event["id"])
            if not _has_automated_official_collector(event):
                unsupported_due_event_ids.append(event["id"])
        else:
            sync_status = "overdue"
            overdue_event_ids.append(event["id"])

        if lower_bound <= event_moment <= upper_bound:
            upcoming.append({
                "id": event["id"],
                "kind": event["kind"],
                "ticker": event.get("ticker"),
                "name": event["name"],
                "scheduled_date": event["scheduled_date"],
                "scheduled_at": event.get("scheduled_at"),
                "monitor_after": event["monitor_after"],
                "schedule_timezone": event.get("schedule_timezone"),
                "schedule_status": event["schedule_status"],
                "time_note": event.get("time_note"),
                "schedule_source_name": event.get("schedule_source_name"),
                "schedule_source_url": event["schedule_source_url"],
                "segments": event.get("segments") or [],
                "sync_status": sync_status,
                "result_collection_status": (
                    "automated-official"
                    if _has_automated_official_collector(event)
                    else "manual-official-review"
                ),
            })

    upcoming.sort(key=_event_moment)
    recent_results = []
    for result in results.get("results", []):
        retrieved_at = _parse_datetime(result["retrieved_at"], "retrieved_at")
        if retrieved_at >= now - timedelta(days=14):
            recent_results.append(_public_result(result, event_by_id.get(result["event_id"])))
    recent_results.sort(
        key=lambda item: _parse_datetime(item["retrieved_at"], "retrieved_at"),
        reverse=True,
    )

    calendar_expires_at = _parse_datetime(calendar["expires_at"], "expires_at")
    calendar_status = "fresh" if calendar_expires_at > now else "stale"
    company_trackers = []
    for monitor in calendar.get("monitors", []):
        if monitor.get("kind") != "earnings":
            continue
        ticker = str(monitor["ticker"]).upper()
        ticker_events = sorted(
            earnings_events_by_ticker.get(ticker, []),
            key=_event_moment,
        )
        future_events = [event for event in ticker_events if _event_moment(event) >= now]
        past_events = [event for event in ticker_events if _event_moment(event) < now]
        selected = future_events[0] if future_events else (past_events[-1] if past_events else None)
        selected_result = result_by_event.get(selected["id"]) if selected else None
        verified_impact = bool(
            selected_result
            and selected_result.get("status") == "complete"
            and selected_result.get("review_status") == "verified"
            and selected_result.get("impact_review")
        )
        if not selected:
            tracker_status = "awaiting_official_date"
        elif future_events and selected.get("scheduled_at"):
            tracker_status = "scheduled"
        elif future_events:
            tracker_status = "date_confirmed"
        elif verified_impact:
            tracker_status = "reviewed"
        else:
            tracker_status = "review_pending"
        company_trackers.append({
            "ticker": ticker,
            "name": monitor["name"],
            "primary_role": monitor["primary_role"],
            "segments": monitor.get("segments") or [],
            "calendar_url": monitor["calendar_url"],
            "tracker_status": tracker_status,
            "impact_review": (
                selected_result.get("impact_review")
                if verified_impact
                else None
            ),
            "event": ({
                "id": selected["id"],
                "name": selected["name"],
                "scheduled_date": selected["scheduled_date"],
                "scheduled_at": selected.get("scheduled_at"),
                "schedule_status": selected["schedule_status"],
                "time_note": selected.get("time_note"),
                "schedule_source_name": selected.get("schedule_source_name"),
                "schedule_source_url": selected["schedule_source_url"],
            } if selected else None),
        })
    company_trackers.sort(key=lambda item: (item["primary_role"], item["name"]))
    scheduled_company_count = sum(
        item["tracker_status"] in {"scheduled", "date_confirmed"}
        for item in company_trackers
    )
    reviewed_company_count = sum(
        item["tracker_status"] == "reviewed" for item in company_trackers
    )
    review_pending_company_count = sum(
        item["tracker_status"] == "review_pending" for item in company_trackers
    )
    awaiting_company_count = sum(
        item["tracker_status"] == "awaiting_official_date"
        for item in company_trackers
    )
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "calendar_generated_at": calendar["generated_at"],
        "calendar_expires_at": calendar["expires_at"],
        "calendar_status": calendar_status,
        "results_generated_at": results["generated_at"],
        "monitored_company_count": sum(
            monitor.get("kind") == "earnings" for monitor in calendar.get("monitors", [])
        ),
        "monitored_macro_count": sum(
            monitor.get("kind") == "macro" for monitor in calendar.get("monitors", [])
        ),
        "scheduled_company_count": scheduled_company_count,
        "reviewed_company_count": reviewed_company_count,
        "review_pending_company_count": review_pending_company_count,
        "awaiting_company_count": awaiting_company_count,
        "company_trackers": company_trackers,
        "upcoming": upcoming,
        "recent_results": recent_results,
        "due_event_ids": due_event_ids,
        "overdue_event_ids": overdue_event_ids,
        "unsupported_due_event_ids": unsupported_due_event_ids,
    }


def build_public_feed(calendar_path=None, results_path=None, now=None) -> dict:
    """일정과 발표 결과를 사이클과 분리된 공식 공개 feed로 만든다."""
    calendar_path = Path(calendar_path or DEFAULT_CALENDAR_PATH)
    results_path = Path(results_path or DEFAULT_RESULTS_PATH)
    calendar = validate_calendar(_read_json(calendar_path))
    results = validate_results(_read_json(results_path), calendar)
    sync = build_event_sync(calendar_path, results_path, now=now)
    content_hash = hashlib.sha256(
        json.dumps(
            {"calendar": calendar, "results": results},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    quality_status = "passed" if sync["calendar_status"] == "fresh" else "stale"
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "feed_id": f"market-events-{content_hash[:16]}",
        "content_sha256": content_hash,
        "generated_at": max(
            _parse_datetime(calendar["generated_at"], "generated_at"),
            _parse_datetime(results["generated_at"], "results generated_at"),
        ).isoformat(timespec="seconds"),
        "expires_at": calendar["expires_at"],
        "quality_gate": {
            "status": quality_status,
            "mode": "official-only",
            "message": (
                "공식 일정과 검증된 발표 결과만 표시합니다."
                if quality_status == "passed"
                else "공식 일정 갱신 기한이 지나 직전값으로 표시합니다."
            ),
        },
        "event_sync": {
            "calendar_status": sync["calendar_status"],
            "monitored_company_count": sync["monitored_company_count"],
            "monitored_macro_count": sync["monitored_macro_count"],
            "due_event_ids": sync["due_event_ids"],
            "overdue_event_ids": sync["overdue_event_ids"],
            "unsupported_due_event_ids": sync["unsupported_due_event_ids"],
            "scheduled_company_count": sync["scheduled_company_count"],
            "reviewed_company_count": sync["reviewed_company_count"],
            "review_pending_company_count": sync["review_pending_company_count"],
            "awaiting_company_count": sync["awaiting_company_count"],
        },
        "shock_policy": {
            "version": calendar["shock_policy"]["version"],
            "notification_time_kst": "07:00",
            "mode": "objective-official-data-only",
        },
        "events": sync["upcoming"][:24],
        "company_trackers": sync["company_trackers"],
        "recent_results": sync["recent_results"][:12],
    }
