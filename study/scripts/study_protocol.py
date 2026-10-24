"""Study evidence lifecycle, publisher identity and interpretation rules.

These rules describe evidence, never trading instructions. Source publication
dates remain immutable when a later review confirms an existing statement.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit


FREQUENCY_DAYS = {"daily": 4, "weekly": 12, "monthly": 45, "quarterly": 120, "policy": 120}
ISSUER_HOSTS = {
    "nvidia.com": "nvidia", "amd.com": "amd", "micron.com": "micron",
    "tsmc.com": "tsmc", "samsung.com": "samsung", "skhynix.com": "skhynix",
    "asml.com": "asml", "microsoft.com": "microsoft", "atmeta.com": "meta",
    "amazon.com": "amazon", "aboutamazon.com": "amazon", "abc.xyz": "alphabet",
    "vertiv.com": "vertiv", "semi.org": "semi", "wsts.org": "wsts",
    "broadcom.com": "broadcom", "oracle.com": "oracle", "intc.com": "intel",
    "appliedmaterials.com": "appliedmaterials", "lamresearch.com": "lamresearch",
    "tel.com": "tokyoelectron", "hanmisemi.com": "hanmisemi", "arista.com": "arista",
    "supermicro.com": "supermicro", "vistracorp.com": "vistra", "palantir.com": "palantir",
}
TICKER_ISSUERS = {
    "nvda": "nvidia", "amd": "amd", "mu": "micron", "tsm": "tsmc",
    "005930": "samsung", "000660": "skhynix", "msft": "microsoft",
    "meta": "meta", "amzn": "amazon", "googl": "alphabet", "vrt": "vertiv",
    "avgo": "broadcom", "orcl": "oracle", "intc": "intel", "amat": "appliedmaterials",
    "lrcx": "lamresearch", "asml": "asml", "8035": "tokyoelectron", "042700": "hanmisemi",
    "anet": "arista", "smci": "supermicro", "vst": "vistra", "pltr": "palantir",
}
KEY_HYPOTHESES = {
    "cloud_capex": ["고객 투자계획의 실제 집행", "클라우드 매출과 투자 대비 현금창출", "투자 축소·지연 근거"],
    "ai_compute_design": ["주요 고객 수요와 출하 전환", "제품 구성·매출총이익률", "수출규제와 공급 제약"],
    "memory_hbm_dram": ["제품별 가격·재고", "HBM 수요와 수익성", "공급 증가·고객 인증"],
    "memory_nand": ["NAND 가격·재고", "수요 회복과 출하", "감산 해제·공급 증가"],
    "foundry_logic": ["가동률·선단공정 수요", "월별 매출의 실현", "고객 투자와 수율"],
    "equipment_materials": ["수주·취소와 매출 전환", "고객 설비투자 집행", "수출규제·납기"],
    "analog_auto_industrial": ["최종 수요와 주문", "유통·고객 재고", "가격·가동률"],
    "power_infrastructure": ["수주잔고와 프로젝트 지연", "납기·이익률", "고객 투자와 현금 전환"],
    "ai_services": ["사용량의 매출 전환", "투자 대비 현금창출", "서비스 원가·경쟁력"],
}


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def evidence_lifecycle(item: dict, today: date) -> dict:
    published = _day(item.get("published_at"))
    if not published or published > today:
        return {"status": "invalid", "reason": "원문 발행일 확인 불가"}
    age = (today - published).days
    frequency = item.get("frequency")
    limit = FREQUENCY_DAYS.get(frequency, 30)
    configured = item.get("max_age_days", limit)
    if not isinstance(configured, int) or not 1 <= configured <= 120:
        return {"status": "invalid", "reason": "자료 유효기간 오류"}
    limit = min(limit, configured)
    deadline = published + timedelta(days=limit)
    for field in ("next_expected_release_at", "valid_until"):
        boundary = _day(item.get(field))
        if boundary:
            deadline = min(deadline, boundary + timedelta(days=2 if field == "next_expected_release_at" else 0))
    if item.get("revoked") is True or item.get("superseded_by"):
        return {"status": "superseded", "reason": "철회 또는 후속 발표로 대체", "valid_until": deadline.isoformat()}
    if today > deadline:
        return {"status": "expired", "reason": "자료 주기 또는 다음 발표 기한 경과", "valid_until": deadline.isoformat()}
    # Retained monthly/quarterly facts need a recent, explicit weekly review.
    # Merely rebuilding a report is not a review and cannot refresh this field.
    if age > 30:
        reviewed = _day(item.get("last_reviewed_at"))
        if not reviewed or reviewed < published or not 0 <= (today - reviewed).days <= 8:
            return {"status": "review_due", "reason": "기존 원문에 대한 주간 반대 근거 재검토 필요", "valid_until": deadline.isoformat()}
    return {"status": "current", "reason": "원문 주기와 검토 기한 충족", "valid_until": deadline.isoformat(), "age_days": age}


def publisher_key(item: dict) -> str:
    explicit = str(item.get("publisher_id") or "").strip().lower()
    host = (urlsplit(str(item.get("source_url") or "")).hostname or "").lower()
    for domain, issuer in ISSUER_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return "issuer:" + issuer
    if explicit:
        return explicit
    family = str(item.get("source_family") or "").lower()
    for ticker, issuer in TICKER_ISSUERS.items():
        if family in {f"event_{ticker}_ir", f"fabless_{ticker}_ir"}:
            return "issuer:" + issuer
    return family


def deduplicate_evidence(items: list[dict]) -> list[dict]:
    selected = {}
    for item in items:
        url = urlsplit(str(item.get("source_url") or ""))
        origin = item.get("origin_release_id") or item.get("origin_event_id") or urlunsplit((url.scheme, url.netloc, url.path, "", ""))
        metric = item.get("metric") or re.sub(r"\s+", " ", str(item.get("fact") or "")).strip().casefold()
        key = (publisher_key(item), str(origin), item.get("segment"), item.get("pillar"), metric)
        previous = selected.get(key)
        if not previous or (str(item.get("last_reviewed_at") or ""), bool(item.get("audit_passed"))) > (str(previous.get("last_reviewed_at") or ""), bool(previous.get("audit_passed"))):
            selected[key] = item
    return sorted(selected.values(), key=lambda item: str(item.get("id")))


def metric_interpretation(metric: str, value: float, fact: dict, event: dict) -> tuple[str, str, str]:
    """An increase is not inherently favorable: retain ambiguous facts neutral."""
    text = metric.lower()
    direction = "positive" if value > 0 else "negative" if value < 0 else "neutral"
    if any(word in text for word in ("inventory", "재고")):
        return "inventory", "neutral", "재고 증감 원인·수요·가격을 함께 검토해야 합니다."
    if any(word in text for word in ("capex", "capacity", "supply", "설비투자", "공급")):
        role = fact.get("economic_role")
        if role == "customer_investment":
            return "demand", direction, "고객 투자 변화이며 공급자 매출 전환을 별도로 확인합니다."
        return "supply", "neutral", "투자 주체·공급 확장·현금 부담의 의미를 별도로 검토해야 합니다."
    if any(word in text for word in ("orders", "backlog", "rpo", "cloud", "azure", "수주")):
        return "demand", direction, "직접 공시된 수요 비교 지표입니다."
    if any(word in text for word in ("price", "asp", "pricing", "가격")):
        return "pricing", direction, "가격 변화이며 비용·제품 구성을 함께 확인합니다."
    if any(word in text for word in ("revenue", "sales", "income", "profit", "earnings", "매출", "이익")):
        return "earnings", direction, "공식 비교 기준의 실적 변화이며 산업 전체로 일반화하지 않습니다."
    return "earnings", "neutral", "지표 의미 사전의 검토가 필요합니다."
