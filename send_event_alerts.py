#!/usr/bin/env python3
"""공식 이벤트 쇼크 수동 재확인용 실행기(정기 스케줄 없음)."""

from notifier import send_due_event_shock_alerts


if __name__ == "__main__":
    send_due_event_shock_alerts()
