#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from event_feed import build_public_feed


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
SEGMENT_IDS = {
    "cloud_capex",
    "ai_compute_design",
    "memory_hbm_dram",
    "memory_nand",
    "foundry_logic",
    "equipment_materials",
    "analog_auto_industrial",
    "power_infrastructure",
    "ai_services",
}
PILLARS = {"demand", "inventory", "pricing", "supply", "earnings"}
STRONG_UNSOURCED_PATTERNS = (
    "Stock SAYO",
    "SAYO",
    "stock-sayo",
    "보조 배지",
    "종목 화면",
    "동일 로직",
    "step1",
    "Terra",
    "Sol",
    "JSON 계약",
    "소비자 호환성",
    "선정조건",
    "PEG ",
    "예상 PER",
    "밸류에이션 여유",
    "주가반영를",
    "가격 기준 통과",
    "가격 기준 점검",
    "사이클 칩 계약",
    "CHIP CONTRACT",
    "세그먼트 칩",
    "세그먼트별",
    "세그먼트 상태",
    "사이클 계약",
    "투자 대상",
    "투자 관점",
    "최대 수혜",
    "가치 저장소",
    "가장 확실한",
    "완판",
    "공급 부족이 2028",
    "코스피 9,300",
    "2,000조",
    "목표주가",
    "가능성 약",
    "우세한 전망",
)
PUBLIC_COPY_FILES = (
    "companies.html",
    "cycle.html",
    "methodology.html",
    "index.html",
    "assets/site.js",
    "assets/quant-sync.js",
)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.has_title = False
        self.ids: set[str] = set()
        self.button_attrs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "title":
            self.has_title = True
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "button":
            self.button_attrs.append(attrs)


def validate_html(path: Path) -> list[str]:
    parser = LinkParser()
    text = path.read_text(encoding="utf-8")
    parser.feed(text)
    errors = []
    if not parser.has_title:
        errors.append(f"{path.name}: title 없음")
    for link in parser.links:
        if link.startswith("#"):
            if link[1:] and link[1:] not in parser.ids:
                errors.append(f"{path.name}: 앵커 대상 없음 {link}")
            continue
        if link.startswith(("http://", "https://", "mailto:")):
            continue
        target = link.split("#", 1)[0]
        if target and not (PUBLIC / target).exists():
            errors.append(f"{path.name}: 링크 대상 없음 {link}")
    if path.name == "companies.html":
        if "data-cycle-cards" not in text:
            errors.append("companies.html: 현재 사이클 상태 노출 없음")
        if "data-event-cards" not in text:
            errors.append("companies.html: 주요 발표 일정 노출 없음")
        if (
            '<section id="event-calendar"' not in text
            or '<details class="event-disclosure">' not in text
            or text.index('<section id="event-calendar"') < text.index('<section id="s7"')
        ):
            errors.append("companies.html: 주요 발표가 종목 하단 접기 영역에 있지 않음")
        if '<main' not in text or 'skip-link' not in text:
            errors.append("companies.html: 본문 건너뛰기 또는 main landmark 없음")
        for attrs in parser.button_attrs:
            if "menu-btn" in attrs.get("class", ""):
                for required in ("aria-expanded", "aria-controls"):
                    if required not in attrs:
                        errors.append(f"companies.html: drawer 버튼 {required} 없음")
        for pattern in STRONG_UNSOURCED_PATTERNS:
            if pattern in text:
                errors.append(f"companies.html: 출처 없는 강한 주장 금지어 포함 {pattern}")
        if "주가반영 Rating" not in text:
            errors.append("companies.html: 주가반영 Rating 설명 없음")
    return errors


def validate_json() -> list[str]:
    errors = []
    quant = json.loads((PUBLIC / "data/quant-latest.json").read_text(encoding="utf-8"))
    cycle = json.loads((PUBLIC / "data/cycle-latest.json").read_text(encoding="utf-8"))
    event_feed = json.loads((PUBLIC / "data/event-latest.json").read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    allowed_domains = {item["allowed_domain"] for item in registry.get("sources", [])}
    source_by_id = {item["id"]: item for item in registry.get("sources", [])}
    if quant.get("schema_version") not in {"1.0", "1.1", "1.2"} or not quant.get("stocks"):
        errors.append("quant-latest.json 계약 또는 종목 목록 오류")
    if cycle.get("schema_version") not in {"1.0", "1.1", "1.2"} or not cycle.get("segments"):
        errors.append("cycle-latest.json 계약 또는 세그먼트 목록 오류")
    expected_event_feed = build_public_feed()
    if event_feed != expected_event_feed:
        errors.append("event-latest.json이 검증된 일정·결과 원본과 일치하지 않음")
    if (
        event_feed.get("schema_version") != "1.0"
        or event_feed.get("quality_gate", {}).get("mode") != "official-only"
        or not re.fullmatch(r"[0-9a-f]{64}", str(event_feed.get("content_sha256", "")))
        or len(event_feed.get("events", [])) > 24
        or len(event_feed.get("recent_results", [])) > 12
        or "unsupported_due_event_ids" not in event_feed.get("event_sync", {})
    ):
        errors.append("event-latest.json 계약 오류")
    if any(
        result.get("shock", {}).get("is_shock") is True
        and (
            result.get("review_status") != "verified"
            or result.get("shock", {}).get("audit_passed") is not True
            or not str(result.get("shock", {}).get("notify_at") or "").endswith("07:00:00+09:00")
        )
        for result in event_feed.get("recent_results", [])
    ):
        errors.append("event-latest.json 쇼크 알림 검증 오류")
    if cycle.get("schema_version") in {"1.1", "1.2"}:
        for field in ("logic_version", "methodology_version", "evidence_sha256", "quality_gate", "fallback_status"):
            if not cycle.get(field):
                errors.append(f"cycle: {field} 누락")
        if not re.fullmatch(r"[0-9a-f]{64}", str(cycle.get("evidence_sha256", ""))):
            errors.append("cycle: evidence_sha256 형식 오류")
        if cycle.get("fallback_status") != "pending-neutral":
            errors.append("cycle: fallback_status 오류")
        quality_gate = cycle.get("quality_gate", {})
        if quality_gate.get("status") not in {"passed", "insufficient"} or quality_gate.get("mode") != "verified-only":
            errors.append("cycle: quality_gate 오류")
        if not cycle.get("evidence") and quality_gate.get("status") != "insufficient":
            errors.append("cycle: 근거 0건을 품질 통과로 표시할 수 없음")
    if cycle.get("schema_version") == "1.2":
        if not cycle.get("company_map_version") or not cycle.get("company_cycle"):
            errors.append("cycle: 종목 노출 계약 누락")
    for document, label in ((quant, "quant"), (cycle, "cycle")):
        for field in ("generated_at", "expires_at"):
            try:
                datetime.fromisoformat(document[field])
            except (KeyError, ValueError):
                errors.append(f"{label}: {field} 형식 오류")
    for segment in cycle.get("segments", []):
        if segment.get("id") not in SEGMENT_IDS:
            errors.append(f"cycle: 알 수 없는 세그먼트 {segment.get('id')}")
        if segment.get("status") not in {"favorable", "neutral", "caution"}:
            errors.append(f"cycle: 잘못된 상태 {segment.get('status')}")
        if not 0 <= int(segment.get("confidence", -1)) <= 85:
            errors.append("cycle: 신뢰도 범위 오류")
    evidence_ids = set()
    for item in cycle.get("evidence", []):
        evidence_id = item.get("id")
        if not evidence_id or evidence_id in evidence_ids:
            errors.append("cycle: 근거 ID 누락 또는 중복")
        evidence_ids.add(evidence_id)
        if urlparse(item.get("source_url", "")).hostname not in allowed_domains:
            errors.append(f"cycle: 허용되지 않은 근거 도메인 {item.get('source_url')}")
        source = source_by_id.get(item.get("source_id"))
        if not source:
            errors.append(f"cycle: 등록되지 않은 source_id {item.get('source_id')}")
        elif urlparse(item.get("source_url", "")).hostname != source.get("allowed_domain") or item.get("source_family") != source.get("source_family"):
            errors.append(f"cycle: 출처 등록정보 불일치 {evidence_id}")
        if item.get("review_status") != "verified" or item.get("confidence") != "direct":
            errors.append(f"cycle: 미검증 근거 포함 {evidence_id}")
        if item.get("segment") not in SEGMENT_IDS:
            errors.append(f"cycle: 근거 세그먼트 오류 {evidence_id}")
        if item.get("pillar") not in PILLARS:
            errors.append(f"cycle: 근거 축 오류 {evidence_id}")
        try:
            datetime.fromisoformat(str(item["published_at"]))
        except (KeyError, ValueError):
            errors.append(f"cycle: 근거 발행일 오류 {evidence_id}")
        if not isinstance(item.get("max_age_days"), int) or not 1 <= item["max_age_days"] <= 120:
            errors.append(f"cycle: 근거 유효기간 오류 {evidence_id}")
    for segment in cycle.get("segments", []):
        for field in ("supporting_evidence_ids", "contrary_evidence_ids"):
            missing = set(segment.get(field, [])) - evidence_ids
            if missing:
                errors.append(f"cycle: {segment.get('id')} {field} 참조 오류 {sorted(missing)}")
        if segment.get("status") in {"favorable", "caution"}:
            selected_ids = set(segment.get("supporting_evidence_ids", []))
            selected = [item for item in cycle.get("evidence", []) if item.get("id") in selected_ids]
            families = {item.get("source_family") for item in selected}
            pillars = {item.get("pillar") for item in selected}
            if len(families) < 2 or len(pillars) < 2:
                errors.append(f"cycle: {segment.get('id')} 상태 확정 근거 부족")
    company_tickers = set()
    for company in cycle.get("company_cycle", []):
        ticker = str(company.get("ticker") or "").upper()
        if not ticker or ticker in company_tickers:
            errors.append(f"cycle: 종목 노출 ticker 누락 또는 중복 {ticker}")
        company_tickers.add(ticker)
        if company.get("status") not in {"favorable", "neutral", "caution"}:
            errors.append(f"cycle: 종목 상태 오류 {ticker}")
        exposures = company.get("exposures") or []
        if not exposures or any(item.get("segment") not in SEGMENT_IDS for item in exposures):
            errors.append(f"cycle: 종목 노출 영역 오류 {ticker}")
        weight_sum = sum(float(item.get("weight", 0)) for item in exposures)
        if abs(weight_sum - 1.0) > 0.01:
            errors.append(f"cycle: 종목 노출도 합계 오류 {ticker}")
    if not cycle.get("evidence") and any(segment.get("status") != "neutral" or segment.get("confidence") != 25 for segment in cycle.get("segments", [])):
        errors.append("cycle: 근거 0건이면 모든 세그먼트는 신뢰도 25의 중립이어야 함")
    if quant.get("alignment", {}).get("drift", 0) > 0:
        errors.append("quant: Stock SAYO 로직 계약 불일치 종목 존재")
    for stock in quant.get("stocks", []):
        ratings = stock.get("ratings", {})
        for key in ("fundamental", "price_reflection"):
            value = ratings.get(key)
            if value is not None and not 1 <= int(value) <= 100:
                errors.append(f"quant: {stock.get('ticker')} {key} Rating 범위 오류")
        if quant.get("schema_version") in {"1.1", "1.2"}:
            if ratings.get("reference_line") != 50:
                errors.append(f"quant: {stock.get('ticker')} Rating 기준선 오류")
            orientation = ratings.get("orientation", {})
            if orientation.get("fundamental") != "higher_is_stronger" or orientation.get("price_reflection") != "higher_is_more_priced_in":
                errors.append(f"quant: {stock.get('ticker')} Rating 방향 오류")
            for key in ("fundamental", "price_reflection"):
                quality = stock.get("rating_quality", {}).get(key, {})
                if quality.get("level") not in {"high", "medium", "low", "unavailable"}:
                    errors.append(f"quant: {stock.get('ticker')} {key} 품질 등급 오류")
        if quant.get("schema_version") == "1.2":
            if not quant.get("rating_logic_version"):
                errors.append("quant: rating_logic_version 누락")
            if not stock.get("data_as_of"):
                errors.append(f"quant: {stock.get('ticker')} 기준일 누락")
            fundamental = stock.get("ratings", {}).get("fundamental")
            fundamental_quality = stock.get("rating_quality", {}).get("fundamental", {})
            if fundamental is not None and int(fundamental_quality.get("available", 0)) < 3:
                errors.append(f"quant: {stock.get('ticker')} 펀더멘털 근거 부족")
    return errors


def validate_public_copy() -> list[str]:
    errors = []
    for name in PUBLIC_COPY_FILES:
        text = (PUBLIC / name).read_text(encoding="utf-8")
        for pattern in STRONG_UNSOURCED_PATTERNS:
            if pattern in text:
                errors.append(f"{name}: 학습 페이지 목적을 흐리는 내부/강한 문구 포함 {pattern}")
    return errors


def main() -> None:
    errors = []
    for name in ("index.html", "companies.html", "cycle.html", "methodology.html"):
        errors.extend(validate_html(PUBLIC / name))
    errors.extend(validate_public_copy())
    errors.extend(validate_json())
    if errors:
        raise SystemExit("\n".join(f"ERROR: {error}" for error in errors))
    print("정적 HTML·JSON 계약 검증 통과")


if __name__ == "__main__":
    main()
