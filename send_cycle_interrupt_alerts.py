#!/usr/bin/env python3
"""검증된 중요 발표 기반 사이클 중간 변경 알림 실행기."""

from notifier import send_due_cycle_interrupt_alerts


if __name__ == "__main__":
    send_due_cycle_interrupt_alerts()
