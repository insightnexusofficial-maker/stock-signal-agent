#!/usr/bin/env python3
"""공식 IR 페이지에서 아직 등록되지 않은 실적 발표일을 보수적으로 찾는다.

정확한 시각은 추정하지 않는다. 공식 페이지의 실적 문맥 가까이에 있는 날짜만
`date_confirmed`로 추가하며, 이미 수동 검증된 향후 일정은 건드리지 않는다.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from event_feed import KST, validate_calendar


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = ROOT / "data" / "event-calendar.json"
KEYWORDS = (
    "earnings",
    "financial results",
    "quarterly results",
    "results call",
    "earnings release",
    "실적",
)
MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
    )
    if name
}
DATE_PATTERNS = (
    re.compile(
        r"\b(?P<month>January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(?P<year>20\d{2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<year>20\d{2})[./-](?P<month_num>\d{1,2})[./-](?P<day>\d{1,2})\b"
    ),
)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            value = " ".join(data.split())
            if value:
                self.text.append(value)


def _visible_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    return " ".join(parser.text)


def _matched_date(match: re.Match) -> date | None:
    try:
        month = (
            MONTHS[match.group("month").lower()]
            if "month" in match.groupdict() and match.group("month")
            else int(match.group("month_num"))
        )
        return date(int(match.group("year")), month, int(match.group("day")))
    except (KeyError, TypeError, ValueError):
        return None


def discover_official_earnings_date(
    html: str,
    today: date,
    horizon_days: int = 180,
) -> date | None:
    text = _visible_text(html)
    lower = text.lower()
    candidates = set()
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            candidate = _matched_date(match)
            if not candidate or not today <= candidate <= today + timedelta(days=horizon_days):
                continue
            # 공식 IR 페이지는 보통 이벤트 제목 뒤에 날짜를 둔다. 날짜 뒤의
            # 다른 이벤트 제목까지 읽으면 연례행사 날짜를 실적으로 오인할 수
            # 있으므로 같은 항목의 앞쪽 문맥만 사용한다.
            context = lower[max(0, match.start() - 140): match.end()]
            if any(keyword in context for keyword in KEYWORDS):
                candidates.add(candidate)
    return min(candidates) if candidates else None


def _fetch(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "stock-sayo-study-public-data/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _date_only_event(monitor: dict, scheduled_date: date, checked_at: datetime) -> dict:
    ticker = str(monitor["ticker"]).upper()
    next_day = scheduled_date + timedelta(days=1)
    return {
        "id": f"earnings-{ticker}-{scheduled_date.isoformat()}",
        "kind": "earnings",
        "ticker": ticker,
        "name": f"{monitor['name']} 실적 발표",
        "scheduled_date": scheduled_date.isoformat(),
        "scheduled_at": None,
        "monitor_after": datetime.combine(
            next_day,
            datetime.min.time(),
            tzinfo=KST,
        ).isoformat(timespec="seconds"),
        "capture_until": datetime.combine(
            next_day + timedelta(days=2),
            datetime.min.time(),
            tzinfo=KST,
        ).isoformat(timespec="seconds"),
        "schedule_timezone": None,
        "schedule_status": "date_confirmed",
        "time_note": "공식 발표일 확인 · 정확한 시각 재확인",
        "schedule_source_name": monitor["name"],
        "schedule_source_url": monitor["calendar_url"],
        "result_source_url": monitor.get("results_url"),
        "segments": monitor.get("segments") or [],
        "discovered_at": checked_at.isoformat(timespec="seconds"),
    }


def discover(now: datetime | None = None, fetcher=_fetch) -> tuple[int, int, int]:
    now = (now or datetime.now(KST)).astimezone(KST)
    calendar = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    validate_calendar(calendar)
    today = now.date()
    existing_future_tickers = {
        str(item.get("ticker") or "").upper()
        for item in calendar.get("events", [])
        if item.get("kind") == "earnings"
        and item.get("ticker")
        and date.fromisoformat(item["scheduled_date"]) >= today
    }
    checked = 0
    failed = 0
    added = 0
    for monitor in calendar.get("monitors", []):
        if monitor.get("kind") != "earnings":
            continue
        ticker = str(monitor["ticker"]).upper()
        if ticker in existing_future_tickers:
            continue
        try:
            html = fetcher(monitor["calendar_url"])
            checked += 1
        except Exception:
            failed += 1
            continue
        candidate = discover_official_earnings_date(html, today)
        if not candidate:
            continue
        calendar.setdefault("events", []).append(_date_only_event(monitor, candidate, now))
        existing_future_tickers.add(ticker)
        added += 1
    # 향후 일정이 없는 모든 공식 IR을 오류 없이 확인했거나 새 일정을
    # 발견했을 때만 캘린더의 유효기간을 연장한다. 일부 출처 실패를
    # 정상 갱신으로 위장하지 않는다.
    if added or (checked and failed == 0):
        calendar["events"].sort(
            key=lambda item: (item.get("scheduled_at") or item["monitor_after"], item["id"])
        )
        calendar["generated_at"] = now.isoformat(timespec="seconds")
        calendar["expires_at"] = (now + timedelta(days=8)).isoformat(timespec="seconds")
        validate_calendar(calendar)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=CALENDAR_PATH.parent,
            delete=False,
        ) as handle:
            json.dump(calendar, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(CALENDAR_PATH)
    return checked, failed, added


def main() -> None:
    checked, failed, added = discover()
    if checked == 0 and failed:
        raise SystemExit("공식 IR 일정을 한 곳도 확인하지 못했습니다.")
    print(f"공식 IR 확인 {checked}개 · 실패 {failed}개 · 새 일정 {added}개")


if __name__ == "__main__":
    main()
