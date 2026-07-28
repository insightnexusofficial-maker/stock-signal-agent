"""검증된 공식 이벤트 쇼크 중 07:00 KST 발송 대상만 고른다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


KST = timezone(timedelta(hours=9))
ALERT_GRACE = timedelta(hours=4)


def _parse_kst(value):
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(KST)


def _current_official_feed(event_feed, now):
    expires_at = _parse_kst(
        event_feed.get("expires_at") if isinstance(event_feed, dict) else None
    )
    return bool(
        isinstance(event_feed, dict)
        and event_feed.get("quality_gate", {}).get("status") == "passed"
        and event_feed.get("quality_gate", {}).get("mode") == "official-only"
        and expires_at is not None
        and expires_at > now
    )


def due_result_alerts(event_feed, now=None):
    """공식 확인이 끝난 주요 발표 결과의 즉시 1회 알림 후보를 반환한다."""
    now = (now or datetime.now(KST)).astimezone(KST)
    if not _current_official_feed(event_feed, now):
        return []

    alerts = []
    for result in event_feed.get("recent_results") or []:
        retrieved_at = _parse_kst(result.get("retrieved_at"))
        source_published_at = _parse_kst(result.get("source_published_at"))
        event_id = str(result.get("event_id") or "")
        source_urls = result.get("source_urls") or []
        if (
            result.get("status") != "complete"
            or result.get("review_status") != "verified"
            or retrieved_at is None
            or source_published_at is None
            or not event_id
            or not source_urls
            or not (retrieved_at <= now <= retrieved_at + ALERT_GRACE)
        ):
            continue
        name = str(result.get("event_name") or event_id)
        summary = str(result.get("summary") or "공식 발표 결과 확인")
        published_label = source_published_at.strftime("%m월 %d일 %H:%M KST")
        alerts.append({
            "event_id": event_id,
            "title": f"📊 공식 발표 확인: {name}",
            "body": f"{summary[:170]} · {published_label}",
            "tag": f"event-result-{event_id}"[:120],
            "data": {
                "type": "event_result",
                "event_id": event_id,
                "ticker": str(result.get("ticker") or ""),
            },
            "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
        })
    return alerts


def due_shock_alerts(event_feed, now=None):
    """공식 검증·감사·쇼크 기준을 모두 통과한 07:00 알림만 반환한다."""
    now = (now or datetime.now(KST)).astimezone(KST)
    if (
        not _current_official_feed(event_feed, now)
        or event_feed.get("shock_policy", {}).get("notification_time_kst") != "07:00"
        or event_feed.get("shock_policy", {}).get("mode") != "objective-official-data-only"
    ):
        return []

    alerts = []
    for result in event_feed.get("recent_results") or []:
        shock = result.get("shock") or {}
        notify_at = _parse_kst(shock.get("notify_at"))
        if (
            result.get("status") != "complete"
            or result.get("review_status") != "verified"
            or shock.get("is_shock") is not True
            or shock.get("severity") != "shock"
            or shock.get("audit_passed") is not True
            or not str(shock.get("rule_id") or "").strip()
            or not str(shock.get("reason") or "").strip()
            or notify_at is None
            or not (notify_at <= now <= notify_at + ALERT_GRACE)
        ):
            continue
        event_id = str(result.get("event_id") or "")
        if not event_id:
            continue
        name = str(result.get("event_name") or event_id)
        reason = str(shock.get("reason") or result.get("summary") or "공식 발표의 객관적 쇼크 기준 충족")
        alerts.append({
            "event_id": event_id,
            "title": f"🚨 주요 발표 쇼크: {name}",
            "body": reason[:220],
            "tag": f"event-shock-{event_id}"[:120],
            "data": {
                "type": "event_shock",
                "event_id": event_id,
                "ticker": str(result.get("ticker") or ""),
                "rule_id": str(shock.get("rule_id") or ""),
            },
            "notify_at": notify_at.isoformat(timespec="seconds"),
        })
    return alerts
