#!/usr/bin/env python3
"""Discover the next 45 days from official release calendars.

Publication times and reference periods are never inferred from convention.
Unsupported official pages remain explicit checks, not invented events.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from event_feed import KST, validate_calendar
from calendar_diagnostics import safe_error

ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = ROOT / "data/event-calendar.json"
MONTHS = {name.lower(): number for number, name in enumerate(("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")) if name}
MONTHS.update({name[:3]: number for name, number in list(MONTHS.items())})
MONTH_PATTERN = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
DATE_PATTERN = re.compile(rf"(?P<month>{MONTH_PATTERN})\s+(?P<day>\d{{1,2}})(?:,)?\s+(?P<year>20\d{{2}})", re.I)
REFERENCE_PATTERN = re.compile(rf"(?P<month>{MONTH_PATTERN})\s+(?P<year>20\d{{2}})", re.I)
TIME_PATTERN = re.compile(r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)\b", re.I)


class CalendarHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.rows = []
        self.row = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if tag == "tr":
            self.row = []

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if tag == "tr" and self.row is not None:
            self.rows.append(" ".join(self.row))
            self.row = None

    def handle_data(self, data):
        if self.ignored or not data.strip():
            return
        value = " ".join(data.split())
        self.text.append(value)
        if self.row is not None:
            self.row.append(value)


def _month(value):
    return MONTHS[value.lower().rstrip(".")]


def _event(monitor, scheduled_date, now, clock=None, reference=None):
    scheduled = datetime.combine(scheduled_date, clock, ZoneInfo("America/New_York")).astimezone(KST) if clock else None
    suffix = reference or scheduled_date.isoformat()
    monitor_after = scheduled + timedelta(minutes=5) if scheduled else datetime.combine(scheduled_date + timedelta(days=1), time.min, KST)
    return {
        "id": monitor["id"] + "-" + suffix,
        "kind": "macro", "name": monitor["name"],
        "scheduled_date": scheduled_date.isoformat(),
        "scheduled_at": scheduled.isoformat(timespec="seconds") if scheduled else None,
        "monitor_after": monitor_after.isoformat(timespec="seconds"),
        "capture_until": (monitor_after + timedelta(days=2)).isoformat(timespec="seconds"),
        "schedule_status": "confirmed" if scheduled else "date_confirmed",
        "schedule_timezone": "America/New_York" if scheduled else None,
        "schedule_source_name": monitor["name"],
        "schedule_source_url": monitor["calendar_url"],
        "result_source_url": monitor.get("results_url") or monitor["calendar_url"],
        "reference_period": reference,
        "time_note": "공식 원문 시각" if scheduled else "공식 날짜 확인 · 정확한 시각 확인 대기",
        "segments": [], "discovered_at": now.isoformat(timespec="seconds"),
    }


def parse_official_calendar(html: str, monitor: dict, now: datetime) -> list[dict]:
    page = CalendarHTML()
    page.feed(html)
    text = " ".join(page.text)
    today, horizon = now.date(), now.date() + timedelta(days=45)
    found = []
    if monitor["id"] == "macro-fomc":
        for year in range(today.year, horizon.year + 1):
            section = re.search(rf"{year}\s+FOMC Meetings(.*?)(?=20\d{{2}}\s+FOMC Meetings|$)", text, re.S)
            if not section:
                continue
            for match in re.finditer(rf"(?P<month>{MONTH_PATTERN})\s+(?P<first>\d{{1,2}})(?:\s*[-–]\s*(?P<last>\d{{1,2}}))?\*?", section.group(1), re.I):
                scheduled = date(year, _month(match["month"]), int(match["last"] or match["first"]))
                if today <= scheduled <= horizon:
                    found.append(_event(monitor, scheduled, now))
        return found
    if monitor["id"] not in {"macro-us-cpi", "macro-us-jobs", "macro-us-pce"}:
        return []
    page_year = re.search(r"\bYear\s+(20\d{2})\b", text)
    for row in page.rows:
        if monitor["id"] == "macro-us-pce" and "personal income and outlays" not in row.lower():
            continue
        match = DATE_PATTERN.search(row)
        if match:
            scheduled = date(int(match["year"]), _month(match["month"]), int(match["day"]))
        elif page_year:
            short = re.search(rf"(?P<month>{MONTH_PATTERN})\s+(?P<day>\d{{1,2}})\b", row, re.I)
            if not short:
                continue
            scheduled = date(int(page_year[1]), _month(short["month"]), int(short["day"]))
        else:
            continue
        if not today <= scheduled <= horizon:
            continue
        references = list(REFERENCE_PATTERN.finditer(row))
        if not references:
            continue
        ref = references[-1]
        reference = f"{int(ref['year']):04d}-{_month(ref['month']):02d}"
        tm = TIME_PATTERN.search(row)
        clock = None
        if tm and re.search(r"Eastern|\bEDT\b|\bEST\b|\bET\b", text, re.I):
            hour = int(tm["hour"]) % 12 + (12 if tm["ampm"].upper() == "PM" else 0)
            clock = time(hour, int(tm["minute"]))
        found.append(_event(monitor, scheduled, now, clock, reference))
    return list({event["id"]: event for event in found}.values())


def fetch_official(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.bls.gov", "www.bea.gov", "www.federalreserve.gov", "www.kostat.go.kr", "www.bok.or.kr"}:
        raise ValueError("Official calendar domain required")
    request = Request(url, headers={"User-Agent": "stock-sayo-official-calendar/1.0"})
    with urlopen(request, timeout=20) as response:
        if urlparse(response.geturl()).hostname != parsed.hostname:
            raise ValueError("Official calendar redirect host mismatch")
        return response.read(2_000_000).decode("utf-8", errors="replace")


def discover(now=None, fetcher=fetch_official):
    now = (now or datetime.now(KST)).astimezone(KST)
    calendar = validate_calendar(json.loads(CALENDAR_PATH.read_text()))
    existing = {item["id"]: item for item in calendar.get("events", [])}
    checks = []
    for monitor in calendar.get("monitors", []):
        if monitor.get("kind") != "macro":
            continue
        if monitor["id"] not in {"macro-us-cpi", "macro-us-jobs", "macro-us-pce", "macro-fomc"}:
            checks.append({"monitor_id": monitor["id"], "status": "manual_official_confirmation_required", "checked_at": None})
            continue
        try:
            html = fetcher(monitor["calendar_url"])
        except Exception as error:
            checks.append({"monitor_id": monitor["id"], "status": "failed", "checked_at": now.isoformat(timespec="seconds"), **safe_error(error, "fetch")})
            continue
        try:
            events = parse_official_calendar(html, monitor, now)
        except Exception as error:
            checks.append({"monitor_id": monitor["id"], "status": "failed", "checked_at": now.isoformat(timespec="seconds"), **safe_error(error, "parse")})
            continue
        checks.append({"monitor_id": monitor["id"], "status": "confirmed" if events else "no_parseable_future_release", "event_count": len(events), "checked_at": now.isoformat(timespec="seconds")})
        for event in events:
            # Never downgrade an exact, previously verified announcement time.
            if existing.get(event["id"], {}).get("scheduled_at") and not event.get("scheduled_at"):
                continue
            existing[event["id"]] = {**existing.get(event["id"], {}), **event}
    calendar["events"] = sorted(existing.values(), key=lambda item: (item.get("scheduled_at") or item["monitor_after"], item["id"]))
    calendar["macro_calendar_checks"] = checks
    # Per-source checks do not extend the shared calendar expiry. The earnings
    # discovery workflow owns that lifecycle and must complete its own checks.
    validate_calendar(calendar)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=CALENDAR_PATH.parent, delete=False) as handle:
        json.dump(calendar, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(CALENDAR_PATH)
    return checks


if __name__ == "__main__":
    checks = discover()
    print("Official macro calendars:", ", ".join(f"{item['monitor_id']}={item['status']}" for item in checks))
    if any(item["status"] == "failed" for item in checks):
        raise SystemExit("Some official calendars could not be checked; partial result only")
