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


def due_shock_alerts(event_feed, now=None):
    """공식 검증·감사·쇼크 기준을 모두 통과한 07:00 알림만 반환한다."""
    now = (now or datetime.now(KST)).astimezone(KST)
    expires_at = _parse_kst(
        event_feed.get("expires_at") if isinstance(event_feed, dict) else None
    )
    if (
        not isinstance(event_feed, dict)
        or event_feed.get("quality_gate", {}).get("status") != "passed"
        or event_feed.get("quality_gate", {}).get("mode") != "official-only"
        or event_feed.get("shock_policy", {}).get("notification_time_kst") != "07:00"
        or event_feed.get("shock_policy", {}).get("mode") != "objective-official-data-only"
        or expires_at is None
        or expires_at <= now
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
