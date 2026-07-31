#!/usr/bin/env python3
"""공개 공식 페이지에서 검증 가능한 거시 발표 결과를 갱신한다.

현재 자동 수집 대상은 Federal Reserve FOMC 성명이다. 인증 키나 세션을
사용하지 않고 공식 HTML 성명만 읽으며, 파싱에 실패하면 기존 결과를
보존하고 실패로 종료한다.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from event_feed import KST, build_event_sync, validate_calendar, validate_results


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = ROOT / "data" / "event-calendar.json"
RESULTS_PATH = ROOT / "data" / "event-results.json"
FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_HOST = "www.federalreserve.gov"
STATEMENT_PATH = re.compile(
    r"^/newsevents/pressreleases/monetary(?P<date>\d{8})a\.htm$"
)
TARGET_RANGE = re.compile(
    r"target range for the federal funds rate at\s+"
    r"(?P<lower>\d+(?:-\d+/\d+|\.\d+)?)\s+to\s+"
    r"(?P<upper>\d+(?:-\d+/\d+|\.\d+)?)\s+percent",
    re.IGNORECASE,
)


class _OfficialPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.text.append(value)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, document: dict) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _fetch_official_html(url: str, timeout: int = 20) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != FED_HOST:
        raise ValueError("Federal Reserve 공식 HTTPS URL만 수집할 수 있습니다.")
    request = Request(
        url,
        headers={"User-Agent": "stock-sayo-study-public-data/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def discover_latest_fomc_statement(calendar_html: str) -> tuple[str, str]:
    parser = _OfficialPageParser()
    parser.feed(calendar_html)
    candidates = []
    for href in parser.links:
        absolute = urljoin(FED_CALENDAR_URL, href)
        parsed = urlparse(absolute)
        match = STATEMENT_PATH.match(parsed.path)
        if parsed.hostname == FED_HOST and match:
            candidates.append((match.group("date"), absolute))
    if not candidates:
        raise ValueError("FOMC 공식 HTML 성명 링크를 찾지 못했습니다.")
    statement_date, statement_url = max(candidates)
    return datetime.strptime(statement_date, "%Y%m%d").date().isoformat(), statement_url


def _number(value: str) -> float:
    if "-" not in value:
        return float(value)
    whole, fraction = value.split("-", 1)
    numerator, denominator = fraction.split("/", 1)
    return float(whole) + float(numerator) / float(denominator)


def parse_fomc_target_range(statement_html: str) -> tuple[float, float]:
    parser = _OfficialPageParser()
    parser.feed(statement_html)
    statement_text = " ".join(parser.text)
    match = TARGET_RANGE.search(statement_text)
    if not match:
        raise ValueError("FOMC 성명에서 목표금리 범위를 확인하지 못했습니다.")
    lower = _number(match.group("lower"))
    upper = _number(match.group("upper"))
    if not 0 <= lower <= upper <= 25:
        raise ValueError("FOMC 목표금리 범위가 허용 범위를 벗어났습니다.")
    return lower, upper


def _decision_at(statement_date: str) -> datetime:
    local_date = datetime.fromisoformat(statement_date)
    eastern = local_date.replace(
        hour=14,
        tzinfo=ZoneInfo("America/New_York"),
    )
    return eastern.astimezone(KST)


def _event_for_statement(statement_date: str, statement_url: str) -> dict:
    decision_at = _decision_at(statement_date)
    return {
        "id": f"macro-fomc-{statement_date}",
        "kind": "macro",
        "name": "FOMC 금리결정",
        "scheduled_date": statement_date,
        "scheduled_at": decision_at.isoformat(timespec="seconds"),
        "monitor_after": (decision_at + timedelta(minutes=5)).isoformat(timespec="seconds"),
        "capture_until": (decision_at + timedelta(days=2)).isoformat(timespec="seconds"),
        "schedule_timezone": "America/New_York",
        "schedule_status": "confirmed",
        "time_note": "14:00 미국 동부시간",
        "schedule_source_name": "Federal Reserve",
        "schedule_source_url": FED_CALENDAR_URL,
        "result_source_url": statement_url,
        "segments": [],
    }


def _previous_target(results: dict, event_id: str) -> tuple[float, float] | None:
    candidates = []
    for item in results.get("results", []):
        if item.get("event_id") == event_id or not str(item.get("event_id", "")).startswith("macro-fomc-"):
            continue
        facts = {fact.get("metric"): fact.get("value") for fact in item.get("facts", [])}
        lower = facts.get("federal_funds_target_range_lower")
        upper = facts.get("federal_funds_target_range_upper")
        if lower is not None and upper is not None:
            candidates.append((item.get("source_published_at", ""), float(lower), float(upper)))
    if not candidates:
        return None
    _, lower, upper = max(candidates)
    return lower, upper


def build_fomc_result(
    statement_date: str,
    statement_url: str,
    lower: float,
    upper: float,
    results: dict,
    retrieved_at: datetime,
) -> dict:
    event_id = f"macro-fomc-{statement_date}"
    previous = _previous_target(results, event_id)
    change_bp = None
    if previous is not None:
        lower_change = round((lower - previous[0]) * 100)
        upper_change = round((upper - previous[1]) * 100)
        if lower_change == upper_change:
            change_bp = lower_change
    facts = [
        {"metric": "federal_funds_target_range_lower", "value": lower, "unit": "percent"},
        {"metric": "federal_funds_target_range_upper", "value": upper, "unit": "percent"},
    ]
    if change_bp is not None:
        facts.append({
            "metric": "federal_funds_target_range_change",
            "value": change_bp,
            "unit": "basis_points",
            "comparison": "previous meeting",
        })
    result = {
        "event_id": event_id,
        "status": "complete",
        "review_status": "verified",
        "retrieved_at": retrieved_at.astimezone(KST).isoformat(timespec="seconds"),
        "source_published_at": _decision_at(statement_date).isoformat(timespec="seconds"),
        "reference_period": f"{statement_date} FOMC decision",
        "summary": (
            "연방공개시장위원회는 연방기금금리 목표 범위를 "
            f"{lower:g}%~{upper:g}%로 결정했다."
        ),
        "facts": facts,
        "source_urls": [statement_url],
    }
    if change_bp is not None and abs(change_bp) >= 50:
        result["shock"] = {
            "is_shock": True,
            "severity": "shock",
            "rule_id": "fomc-rate-change-50bp",
            "reason": f"직전 회의 대비 목표금리 범위가 {change_bp:+d}bp 변동했다.",
            "audit_passed": True,
        }
    return result


def collect(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    sync = build_event_sync(CALENDAR_PATH, RESULTS_PATH, now=now)
    if not any(
        str(event_id).startswith("macro-fomc-")
        for event_id in sync["due_event_ids"]
    ):
        return False
    calendar = validate_calendar(_read_json(CALENDAR_PATH))
    results = validate_results(_read_json(RESULTS_PATH), calendar)
    calendar_html = _fetch_official_html(FED_CALENDAR_URL)
    statement_date, statement_url = discover_latest_fomc_statement(calendar_html)
    if _decision_at(statement_date) > now:
        return False
    statement_html = _fetch_official_html(statement_url)
    lower, upper = parse_fomc_target_range(statement_html)
    event_id = f"macro-fomc-{statement_date}"

    events = calendar.setdefault("events", [])
    existing_event = next((item for item in events if item.get("id") == event_id), None)
    calendar_changed = False
    if existing_event is None:
        events.append(_event_for_statement(statement_date, statement_url))
        events.sort(key=lambda item: (item.get("scheduled_at") or item["monitor_after"], item["id"]))
        calendar_changed = True
    elif existing_event.get("result_source_url") != statement_url:
        existing_event["result_source_url"] = statement_url
        calendar_changed = True

    current_result = next(
        (item for item in results.get("results", []) if item.get("event_id") == event_id),
        None,
    )
    candidate = build_fomc_result(
        statement_date,
        statement_url,
        lower,
        upper,
        results,
        now,
    )
    if current_result:
        # 공식 성명의 핵심 값이나 URL이 바뀌지 않았다면 수집 시각만으로 파일을
        # 매시간 다시 쓰지 않는다.
        current_facts = {
            fact.get("metric"): fact.get("value")
            for fact in current_result.get("facts", [])
        }
        same_target = (
            current_facts.get("federal_funds_target_range_lower") == lower
            and current_facts.get("federal_funds_target_range_upper") == upper
        )
        if (
            same_target
            and current_result.get("source_urls") == [statement_url]
            and current_result.get("source_published_at") == candidate["source_published_at"]
        ):
            validate_calendar(calendar)
            validate_results(results, calendar)
            if calendar_changed:
                _atomic_write(CALENDAR_PATH, calendar)
            return calendar_changed
        results["results"] = [
            candidate if item.get("event_id") == event_id else item
            for item in results.get("results", [])
        ]
    else:
        results.setdefault("results", []).append(candidate)
    results["results"].sort(key=lambda item: (item.get("source_published_at") or "", item["event_id"]))
    results["generated_at"] = now.isoformat(timespec="seconds")
    validate_calendar(calendar)
    validate_results(results, calendar)
    _atomic_write(CALENDAR_PATH, calendar)
    _atomic_write(RESULTS_PATH, results)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="공식 페이지를 검사하되 파일은 바꾸지 않습니다.",
    )
    args = parser.parse_args()
    if args.check_only:
        calendar_html = _fetch_official_html(FED_CALENDAR_URL)
        statement_date, statement_url = discover_latest_fomc_statement(calendar_html)
        lower, upper = parse_fomc_target_range(_fetch_official_html(statement_url))
        print(f"FOMC {statement_date}: {lower:g}%~{upper:g}%")
        return
    changed = collect()
    print("FOMC 공식 결과 갱신" if changed else "FOMC 공식 결과 변경 없음")


if __name__ == "__main__":
    main()
