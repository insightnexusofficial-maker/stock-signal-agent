#!/usr/bin/env python3
"""현재 시각이 공식 발표 결과 확인 창인지 빠르게 판정한다."""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path

from event_feed import KST, build_event_sync


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = ROOT / "data" / "event-calendar.json"


def due_event_ids(now: datetime | None = None) -> list[str]:
    sync = build_event_sync(now=now or datetime.now(KST))
    due = set(sync["due_event_ids"])
    calendar = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    return [
        event["id"]
        for event in calendar.get("events", [])
        if event["id"] in due
        and event.get("kind") in {"earnings", "macro"}
    ]


def backfill_event_ids(now: datetime | None = None) -> list[str]:
    """당일 창에서 놓친 미검증 실적만 3일 동안 일 1회 재확인한다."""

    now = (now or datetime.now(KST)).astimezone(KST)
    sync = build_event_sync(now=now)
    overdue = set(sync["overdue_event_ids"])
    calendar = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    backfill = []
    for event in calendar.get("events", []):
        if event["id"] not in overdue or event.get("kind") != "earnings":
            continue
        if event.get("scheduled_at"):
            deadline = datetime.fromisoformat(event["scheduled_at"]).astimezone(KST) + timedelta(days=3)
        else:
            scheduled_date = datetime.fromisoformat(event["scheduled_date"]).date()
            deadline = datetime.combine(
                scheduled_date + timedelta(days=3),
                time.max,
                tzinfo=KST,
            )
        if now <= deadline:
            backfill.append(event["id"])
    return backfill


def main() -> None:
    event_ids = due_event_ids()
    backfill_ids = backfill_event_ids()
    active = bool(event_ids)
    backfill_active = bool(backfill_ids)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"active={'true' if active else 'false'}\n")
            handle.write(f"event_ids={','.join(event_ids)}\n")
            handle.write(
                f"backfill_active={'true' if backfill_active else 'false'}\n"
            )
            handle.write(f"backfill_event_ids={','.join(backfill_ids)}\n")
    print(
        f"발표 결과 확인 창 {len(event_ids)}건 · 제한 백필 {len(backfill_ids)}건"
    )


if __name__ == "__main__":
    main()
