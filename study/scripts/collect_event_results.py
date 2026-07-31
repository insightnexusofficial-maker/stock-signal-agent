#!/usr/bin/env python3
"""공개 공식 페이지에서 검증 가능한 거시 발표 결과를 갱신한다.

자동 수집 대상은 FOMC, 미국 CPI·고용, PCE다. 인증 키나 세션 없이
Federal Reserve·BLS·BEA의 공식 공개 자료만 읽으며, 파싱에 실패하면
기존 결과를 보존한다.
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
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BEA_HOST = "www.bea.gov"
STATEMENT_PATH = re.compile(
    r"^/newsevents/pressreleases/monetary(?P<date>\d{8})a\.htm$"
)
TARGET_RANGE = re.compile(
    r"target range for the federal funds rate at\s+"
    r"(?P<lower>\d+(?:-\d+/\d+|\.\d+)?)\s+to\s+"
    r"(?P<upper>\d+(?:-\d+/\d+|\.\d+)?)\s+percent",
    re.IGNORECASE,
)
PCE_MOM = re.compile(
    r"From the preceding month, the PCE price index for \w+ "
    r"(?P<direction>increased|decreased) (?P<value>\d+(?:\.\d+)?) percent.*?"
    r"Excluding food and energy, the PCE price index "
    r"(?P<core_direction>increased|decreased) (?P<core_value>\d+(?:\.\d+)?) percent",
    re.IGNORECASE,
)
PCE_YOY = re.compile(
    r"From the same month one year ago, the PCE price index for \w+ "
    r"(?P<direction>increased|decreased) (?P<value>\d+(?:\.\d+)?) percent.*?"
    r"Excluding food and energy, the PCE price index "
    r"(?P<core_direction>increased|decreased) (?P<core_value>\d+(?:\.\d+)?) percent",
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


def _fetch_bea_html(url: str, timeout: int = 20) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != BEA_HOST:
        raise ValueError("BEA 공식 HTTPS URL만 수집할 수 있습니다.")
    request = Request(url, headers={"User-Agent": "stock-sayo-study-public-data/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_bls_series(series_ids: list[str], start_year: int, end_year: int) -> dict:
    body = json.dumps({
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
    }).encode("utf-8")
    request = Request(
        BLS_API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "stock-sayo-study-public-data/1.0",
        },
    )
    with urlopen(request, timeout=20) as response:
        document = json.load(response)
    if document.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS 공식 API 요청이 실패했습니다.")
    output = {}
    for series in document.get("Results", {}).get("series", []):
        values = {}
        for item in series.get("data", []):
            period = str(item.get("period") or "")
            if not re.fullmatch(r"M(?:0[1-9]|1[0-2])", period):
                continue
            values[f"{item['year']}-{period[1:]}"] = float(item["value"])
        output[series.get("seriesID")] = values
    return output


def _month_shift(value: str, offset: int) -> str:
    year, month = map(int, value.split("-"))
    index = year * 12 + month - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _event_reference_month(event: dict) -> str:
    match = re.search(r"(20\d{2})-(0[1-9]|1[0-2])$", str(event.get("id") or ""))
    if not match:
        raise ValueError("거시 이벤트 기준월을 확인하지 못했습니다.")
    return match.group(0)


def _signed(direction: str, value: str) -> float:
    number = float(value)
    return -number if direction.lower() == "decreased" else number


def _previous_fact(results: dict, prefix: str, metric: str, event_id: str) -> float | None:
    candidates = []
    for item in results.get("results", []):
        if item.get("event_id") == event_id or not str(item.get("event_id", "")).startswith(prefix):
            continue
        for fact in item.get("facts", []):
            if fact.get("metric") == metric:
                candidates.append((item.get("reference_period", ""), float(fact["value"])))
    return max(candidates, default=(None, None))[1]


def _inflation_shock(mom: float, yoy: float, previous_yoy: float | None) -> dict | None:
    if abs(mom) >= 0.6:
        return {
            "is_shock": True, "severity": "shock",
            "rule_id": "macro-inflation-mom-0_6pct",
            "reason": f"공식 물가지표 전월 대비 변동률이 {mom:+.1f}%로 쇼크 기준을 넘었다.",
            "audit_passed": True,
        }
    if previous_yoy is not None and abs(yoy - previous_yoy) >= 0.5:
        return {
            "is_shock": True, "severity": "shock",
            "rule_id": "macro-inflation-yoy-change-0_5pp",
            "reason": f"공식 물가지표 전년 대비 상승률이 직전 발표보다 {yoy - previous_yoy:+.1f}%p 변했다.",
            "audit_passed": True,
        }
    return None


def build_jobs_result(event: dict, series: dict, results: dict, retrieved_at: datetime) -> dict:
    reference = _event_reference_month(event)
    previous = _month_shift(reference, -1)
    payroll = series["CES0000000001"]
    unemployment = series["LNS14000000"]
    payroll_change = round(payroll[reference] - payroll[previous])
    unemployment_change = round(unemployment[reference] - unemployment[previous], 1)
    result = {
        "event_id": event["id"], "status": "complete", "review_status": "verified",
        "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
        "source_published_at": event["scheduled_at"], "reference_period": reference,
        "summary": f"미국 비농업 고용은 전월보다 {payroll_change:+,d}천 명, 실업률은 {unemployment[reference]:.1f}%로 발표됐다.",
        "facts": [
            {"metric": "nonfarm_payroll_change", "value": payroll_change, "unit": "thousand_people", "comparison": "previous month"},
            {"metric": "unemployment_rate", "value": unemployment[reference], "unit": "percent"},
            {"metric": "unemployment_rate_change", "value": unemployment_change, "unit": "percentage_points", "comparison": "previous month"},
        ],
        "source_urls": [BLS_API_URL],
    }
    if abs(payroll_change) >= 500:
        result["shock"] = {"is_shock": True, "severity": "shock", "rule_id": "macro-payroll-change-500k", "reason": f"비농업 고용 증감이 {payroll_change:+,d}천 명으로 쇼크 기준을 넘었다.", "audit_passed": True}
    elif abs(unemployment_change) >= 0.5:
        result["shock"] = {"is_shock": True, "severity": "shock", "rule_id": "macro-unemployment-change-0_5pp", "reason": f"실업률이 전월보다 {unemployment_change:+.1f}%p 변해 쇼크 기준을 넘었다.", "audit_passed": True}
    return result


def build_cpi_result(event: dict, series: dict, results: dict, retrieved_at: datetime) -> dict:
    reference = _event_reference_month(event)
    previous = _month_shift(reference, -1)
    year_ago = _month_shift(reference, -12)
    prior_year_ago = _month_shift(reference, -13)
    headline = series["CUSR0000SA0"]
    core = series["CUSR0000SA0L1E"]
    mom = round((headline[reference] / headline[previous] - 1) * 100, 1)
    yoy = round((headline[reference] / headline[year_ago] - 1) * 100, 1)
    core_mom = round((core[reference] / core[previous] - 1) * 100, 1)
    core_yoy = round((core[reference] / core[year_ago] - 1) * 100, 1)
    prior_yoy = round((headline[previous] / headline[prior_year_ago] - 1) * 100, 1)
    result = {
        "event_id": event["id"], "status": "complete", "review_status": "verified",
        "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
        "source_published_at": event["scheduled_at"], "reference_period": reference,
        "summary": f"미국 CPI는 전월 대비 {mom:+.1f}%, 전년 대비 {yoy:+.1f}%로 발표됐다.",
        "facts": [
            {"metric": "cpi_mom", "value": mom, "unit": "percent", "comparison": "previous month"},
            {"metric": "cpi_yoy", "value": yoy, "unit": "percent", "comparison": "same month previous year"},
            {"metric": "core_cpi_mom", "value": core_mom, "unit": "percent", "comparison": "previous month"},
            {"metric": "core_cpi_yoy", "value": core_yoy, "unit": "percent", "comparison": "same month previous year"},
        ],
        "source_urls": [BLS_API_URL],
    }
    shock = _inflation_shock(mom, yoy, prior_yoy)
    if shock:
        result["shock"] = shock
    return result


def build_pce_result(event: dict, html: str, results: dict, retrieved_at: datetime) -> dict:
    parser = _OfficialPageParser()
    parser.feed(html)
    text = " ".join(parser.text)
    mom_match = PCE_MOM.search(text)
    yoy_match = PCE_YOY.search(text)
    if not mom_match or not yoy_match:
        raise ValueError("BEA 발표문에서 PCE 물가지표를 확인하지 못했습니다.")
    mom = _signed(mom_match.group("direction"), mom_match.group("value"))
    core_mom = _signed(mom_match.group("core_direction"), mom_match.group("core_value"))
    yoy = _signed(yoy_match.group("direction"), yoy_match.group("value"))
    core_yoy = _signed(yoy_match.group("core_direction"), yoy_match.group("core_value"))
    reference = _event_reference_month(event)
    result = {
        "event_id": event["id"], "status": "complete", "review_status": "verified",
        "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
        "source_published_at": event["scheduled_at"], "reference_period": reference,
        "summary": f"미국 PCE 물가지수는 전월 대비 {mom:+.1f}%, 전년 대비 {yoy:+.1f}%로 발표됐다.",
        "facts": [
            {"metric": "pce_price_index_mom", "value": mom, "unit": "percent", "comparison": "previous month"},
            {"metric": "pce_price_index_yoy", "value": yoy, "unit": "percent", "comparison": "same month previous year"},
            {"metric": "core_pce_price_index_mom", "value": core_mom, "unit": "percent", "comparison": "previous month"},
            {"metric": "core_pce_price_index_yoy", "value": core_yoy, "unit": "percent", "comparison": "same month previous year"},
        ],
        "source_urls": [event["result_source_url"]],
    }
    shock = _inflation_shock(mom, yoy, _previous_fact(results, "macro-us-pce-", "pce_price_index_yoy", event["id"]))
    if shock:
        result["shock"] = shock
    return result


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
    due_ids = set(sync["due_event_ids"])
    calendar = validate_calendar(_read_json(CALENDAR_PATH))
    results = validate_results(_read_json(RESULTS_PATH), calendar)
    result_ids = {item["event_id"] for item in results.get("results", [])}
    other_changed = False
    for event in calendar.get("events", []):
        event_id = event["id"]
        if event_id not in due_ids or event_id in result_ids:
            continue
        try:
            if event_id.startswith("macro-us-jobs-"):
                reference = _event_reference_month(event)
                year = int(reference[:4])
                series = _fetch_bls_series(["CES0000000001", "LNS14000000"], year - 1, year)
                candidate = build_jobs_result(event, series, results, now)
            elif event_id.startswith("macro-us-cpi-"):
                reference = _event_reference_month(event)
                year = int(reference[:4])
                series = _fetch_bls_series(["CUSR0000SA0", "CUSR0000SA0L1E"], year - 1, year)
                candidate = build_cpi_result(event, series, results, now)
            elif event_id.startswith("macro-us-pce-"):
                candidate = build_pce_result(
                    event, _fetch_bea_html(event["result_source_url"]), results, now
                )
            else:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        results.setdefault("results", []).append(candidate)
        result_ids.add(event_id)
        other_changed = True
    if other_changed:
        results["results"].sort(key=lambda item: (item.get("source_published_at") or "", item["event_id"]))
        results["generated_at"] = now.isoformat(timespec="seconds")
        validate_results(results, calendar)
        _atomic_write(RESULTS_PATH, results)

    if not any(str(event_id).startswith("macro-fomc-") for event_id in due_ids):
        return other_changed
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
            return calendar_changed or other_changed
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
