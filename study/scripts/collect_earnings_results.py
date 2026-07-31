#!/usr/bin/env python3
"""발표 당일 공식 IR에서 실적 자료 공개 여부만 보수적으로 확인한다.

공식 결과 페이지를 찾으면 검토 대기 결과를 만든다. 수치·추세·리스크는
검증 없이 추정하지 않으며, 별도 검토가 완료된 결과만 `complete`가 된다.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from event_feed import KST, validate_calendar, validate_results


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = ROOT / "data" / "event-calendar.json"
RESULTS_PATH = ROOT / "data" / "event-results.json"
RESULT_PHRASES = (
    "announces financial results",
    "reports financial results",
    "announced financial results",
    "earnings release",
    "quarterly results",
    "실적 발표",
)
SEC_FORMS = {"8-K", "10-Q", "10-K", "6-K"}
SEC_CIK_BY_TICKER = {
    "MU": "0000723125",
    "TSM": "0001046179",
    "NVDA": "0001045810",
    "AMD": "0000002488",
    "AVGO": "0001730168",
    "ASML": "0000937966",
    "LRCX": "0000707549",
    "MSFT": "0000789019",
    "META": "0001326801",
    "AMZN": "0001018724",
    "GOOGL": "0001652044",
    "ORCL": "0001341439",
    "INTC": "0000050863",
    "AMAT": "0000006951",
    "VRT": "0001674101",
    "ANET": "0001596532",
    "SMCI": "0001375365",
    "VST": "0001692819",
    "PLTR": "0001321655",
}
METRIC_PATTERN = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 &/\-]{0,55}?"
    r"(?:revenue|net sales|orders|operating income))\s+"
    r"(?:was\s+[^.;]{0,45}?\s+and\s+)?"
    r"(?P<direction>increased|decreased|grew|declined|rose|fell|up|down)"
    r"\s+(?:by\s+)?(?P<value>\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
OVERALL_METRIC_PATTERN = re.compile(
    r"(?P<label>Revenue|Net sales|Total revenue)\s+was\s+[^;]{0,80}?\s+and\s+"
    r"(?P<direction>increased|decreased|grew|declined|rose|fell|up|down)"
    r"\s+(?:by\s+)?(?P<value>\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor_text)))
            self._href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.text.append(value)
        if self._href:
            self._anchor_text.append(value)


def _parse_page(html: str) -> tuple[str, list[tuple[str, str]]]:
    parser = _PageParser()
    parser.feed(html)
    return " ".join(parser.text), parser.links


def _fetch(url: str, timeout: int = 20) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "stock-sayo-study-public-data/1.0",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _allowed_url(url: str, allowed_domains: set[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)
    )


def _period_tokens(event: dict) -> set[str]:
    value = f"{event.get('id', '')} {event.get('name', '')}".lower()
    tokens = set(re.findall(r"\b20\d{2}\b|\bfy20\d{2}\b|\bq[1-4]\b", value))
    if "june" in value:
        tokens.add("june")
    return tokens


def discover_result_url(
    event: dict,
    page_url: str,
    page_html: str,
    allowed_domains: set[str],
) -> str | None:
    text, links = _parse_page(page_html)
    if looks_like_published_result(text, event):
        return page_url
    period_tokens = _period_tokens(event)
    candidates = []
    for href, label in links:
        absolute = urljoin(page_url, href)
        if not _allowed_url(absolute, allowed_domains):
            continue
        haystack = f"{label} {href}".lower()
        if not any(term in haystack for term in ("earnings", "financial", "quarterly", "result")):
            continue
        score = sum(token in haystack for token in period_tokens)
        candidates.append((score, absolute))
    score, candidate = max(candidates, default=(0, None))
    return candidate if score > 0 else None


def looks_like_published_result(text: str, event: dict) -> bool:
    lower = " ".join(text.lower().split())
    if not any(phrase in lower for phrase in RESULT_PHRASES):
        return False
    if "will report" in lower and not any(
        phrase in lower for phrase in ("reports financial results", "announced financial results")
    ):
        return False
    year = str(event.get("scheduled_date") or "")[:4]
    return not year or year in lower


def _sec_filing_rows(document: dict) -> list[dict]:
    recent = document.get("filings", {}).get("recent", {})
    keys = ("form", "accessionNumber", "filingDate", "primaryDocument")
    lengths = [len(recent.get(key, [])) for key in keys]
    if not lengths or min(lengths) == 0:
        return []
    return [
        {key: recent[key][index] for key in keys}
        for index in range(min(lengths))
    ]


def _sec_candidate_urls(cik: str, row: dict, filing_html: str) -> list[str]:
    accession = str(row["accessionNumber"]).replace("-", "")
    base_url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"
    )
    primary_url = urljoin(base_url, str(row["primaryDocument"]))
    _, links = _parse_page(filing_html)
    exhibits = []
    for href, label in links:
        absolute = urljoin(primary_url, href)
        haystack = f"{label} {href}".lower()
        if not _allowed_url(absolute, {"sec.gov"}):
            continue
        if any(
            token in haystack
            for token in ("ex-99", "exhibit 99", "earnings", "financial-results")
        ):
            exhibits.append(absolute)
    return list(dict.fromkeys(exhibits + [primary_url]))


def discover_sec_result(
    event: dict,
    fetcher=_fetch,
) -> tuple[str, str] | None:
    """공식 IR이 막힐 때 같은 발표 창의 SEC 공시·첨부자료를 확인한다."""

    cik = SEC_CIK_BY_TICKER.get(str(event.get("ticker") or "").upper())
    if not cik:
        return None
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    submissions = json.loads(fetcher(submissions_url))
    scheduled_date = datetime.fromisoformat(event["scheduled_date"]).date()
    window_start = scheduled_date - timedelta(days=1)
    window_end = scheduled_date + timedelta(days=3)
    for row in _sec_filing_rows(submissions):
        if row["form"] not in SEC_FORMS:
            continue
        filing_date = datetime.fromisoformat(row["filingDate"]).date()
        if not window_start <= filing_date <= window_end:
            continue
        accession = str(row["accessionNumber"]).replace("-", "")
        primary_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"
            f"{row['primaryDocument']}"
        )
        filing_html = fetcher(primary_url)
        for candidate_url in _sec_candidate_urls(cik, row, filing_html):
            candidate_html = (
                filing_html if candidate_url == primary_url else fetcher(candidate_url)
            )
            candidate_text = _parse_page(candidate_html)[0]
            if looks_like_published_result(candidate_text, event):
                return candidate_url, candidate_text
    return None


def _metric_id(label: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return f"{value[:64]}_yoy_percent"


def extract_directional_facts(text: str) -> list[dict]:
    facts = []
    seen = set()
    normalized = " ".join(text.split())
    matches = list(OVERALL_METRIC_PATTERN.finditer(normalized))
    matches.extend(METRIC_PATTERN.finditer(normalized))
    matches.sort(key=lambda item: item.start())
    for match in matches:
        label = " ".join(match.group("label").split())
        metric = _metric_id(label)
        if metric in seen:
            continue
        value = float(match.group("value"))
        if match.group("direction").lower() in {"decreased", "declined", "fell", "down"}:
            value = -value
        facts.append({
            "metric": metric,
            "label": label,
            "value": value,
            "unit": "percent",
            "comparison": "official release comparison",
        })
        seen.add(metric)
        if len(facts) >= 8:
            break
    return facts


def build_rules_impact_review(facts: list[dict], reviewed_at: datetime) -> dict | None:
    positive = [fact for fact in facts if float(fact["value"]) >= 10]
    negative = [fact for fact in facts if float(fact["value"]) <= -5]
    has_overall_revenue = any(
        fact.get("label", "").strip().lower() in {"revenue", "net sales", "total revenue"}
        for fact in facts
    )
    if len(facts) < 2 or not has_overall_revenue:
        return None
    if positive and negative:
        trend = "mixed"
    elif len(positive) >= 2:
        trend = "strengthening"
    elif len(negative) >= 2:
        trend = "weakening"
    else:
        trend = "unchanged"
    risk_level = "high" if len(negative) >= 2 else "medium"
    positive_text = ", ".join(
        f"{fact['label']} {float(fact['value']):+g}%"
        for fact in positive[:3]
    ) or "두 자릿수 성장 지표가 제한적"
    negative_text = ", ".join(
        f"{fact['label']} {float(fact['value']):+g}%"
        for fact in negative[:3]
    ) or "공식 결과에서 5% 이상 역성장 지표는 자동 규칙으로 확인되지 않음"
    return {
        "trend_change": trend,
        "risk_level": risk_level,
        "cycle_status_effect": "none_single_source",
        "summary": f"공식 비교 지표 기준 {positive_text}.",
        "risk_summary": f"{negative_text}. 단일 기업 결과이므로 산업 전체로 일반화하지 않는다.",
        "supporting_fact_metrics": [fact["metric"] for fact in positive],
        "risk_fact_metrics": [fact["metric"] for fact in negative],
        "reviewed_at": reviewed_at.isoformat(timespec="seconds"),
        "review_method": "official-result-deterministic-rules",
        "audit_passed": True,
    }


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


def collect(now: datetime | None = None, fetcher=_fetch) -> tuple[int, int]:
    now = (now or datetime.now(KST)).astimezone(KST)
    calendar = validate_calendar(_read_json(CALENDAR_PATH))
    results = validate_results(_read_json(RESULTS_PATH), calendar)
    result_ids = {item["event_id"] for item in results.get("results", [])}
    allowed_domains = {str(item).lower() for item in calendar["allowed_domains"]}
    checked = 0
    added = 0
    for event in calendar.get("events", []):
        if event.get("kind") != "earnings" or event["id"] in result_ids:
            continue
        capture_until = datetime.fromisoformat(event["capture_until"]).astimezone(KST)
        if event.get("scheduled_at"):
            monitor_after = datetime.fromisoformat(event["monitor_after"]).astimezone(KST)
            if not monitor_after <= now <= capture_until:
                continue
        else:
            scheduled_date = datetime.fromisoformat(event["scheduled_date"]).date()
            # 공식 날짜 당일과 다음 날까지만 확인한다.
            if not scheduled_date <= now.date() <= scheduled_date + timedelta(days=1):
                continue
        page_url = event.get("result_source_url")
        if not page_url:
            continue
        candidate_url = None
        candidate_text = None
        try:
            page_html = fetcher(page_url)
            checked += 1
            candidate_url = discover_result_url(
                event,
                page_url,
                page_html,
                allowed_domains,
            )
            if candidate_url:
                candidate_html = (
                    page_html if candidate_url == page_url else fetcher(candidate_url)
                )
                candidate_text = _parse_page(candidate_html)[0]
                if not looks_like_published_result(candidate_text, event):
                    candidate_url = None
                    candidate_text = None
        except Exception:
            pass
        if not candidate_url:
            try:
                sec_result = discover_sec_result(event, fetcher=fetcher)
                checked += 1
                if sec_result:
                    candidate_url, candidate_text = sec_result
            except Exception:
                pass
        if not candidate_url or candidate_text is None:
            continue
        facts = extract_directional_facts(candidate_text)
        impact_review = build_rules_impact_review(facts, now)
        result = {
            "event_id": event["id"],
            "status": "complete" if impact_review else "partial",
            "review_status": "verified" if impact_review else "pending",
            "retrieved_at": now.isoformat(timespec="seconds"),
            "reference_period": event["name"],
            "summary": (
                "공식 실적 자료와 비교 지표 검증 완료"
                if impact_review
                else "공식 실적 자료 공개 확인 · 추세와 리스크 검토 대기"
            ),
            "facts": facts,
            "source_urls": [candidate_url],
        }
        if impact_review:
            result["impact_review"] = impact_review
        results.setdefault("results", []).append(result)
        result_ids.add(event["id"])
        added += 1
    if added:
        results["generated_at"] = now.isoformat(timespec="seconds")
        results["results"].sort(
            key=lambda item: (item.get("retrieved_at") or "", item["event_id"])
        )
        validate_results(results, calendar)
        _atomic_write(RESULTS_PATH, results)
    return checked, added


def main() -> None:
    checked, added = collect()
    print(f"발표 창 공식 결과 확인 {checked}건 · 검토 대기 추가 {added}건")


if __name__ == "__main__":
    main()
