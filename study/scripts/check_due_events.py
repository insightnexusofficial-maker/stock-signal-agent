#!/usr/bin/env python3
"""현재 시각이 공식 발표 결과 확인 창인지 빠르게 판정한다."""

from __future__ import annotations

import json
import os
from datetime import datetime
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


def main() -> None:
    event_ids = due_event_ids()
    active = bool(event_ids)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"active={'true' if active else 'false'}\n")
            handle.write(f"event_ids={','.join(event_ids)}\n")
    print(
        f"발표 결과 확인 창 {len(event_ids)}건"
        if active
        else "현재 발표 결과 확인 대상 없음"
    )


if __name__ == "__main__":
    main()
