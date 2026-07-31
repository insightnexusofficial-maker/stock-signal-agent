from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from typing import Any


KR_FORWARD_PER_SOURCES = {"naver_consensus", "fnguide_multi_year_consensus"}


def as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def decode_firestore_value(value: dict[str, Any]) -> Any:
    if "mapValue" in value:
        fields = value["mapValue"].get("fields", {})
        return {key: decode_firestore_value(item) for key, item in fields.items()}
    if "arrayValue" in value:
        return [decode_firestore_value(item) for item in value["arrayValue"].get("values", [])]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "nullValue" in value:
        return None
    for key in ("stringValue", "booleanValue", "timestampValue", "referenceValue"):
        if key in value:
            return value[key]
    return None


def decode_firestore_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: decode_firestore_value(value)
        for key, value in document.get("fields", {}).items()
    }


def valuation_gate(stock: dict[str, Any], criteria: dict[str, Any], market: str) -> dict[str, Any]:
    if stock.get("metric_quality") == "warning" or any(
        str(warning).startswith("peg:") for warning in stock.get("metric_warnings", [])
    ):
        return {"status": "pending", "basis": "metric_quality", "reason": "검증 경고"}

    peg = as_number(stock.get("peg_fwd"))
    per_fwd = as_number(stock.get("per_fwd"))
    per_ttm = as_number(stock.get("per_ttm"))
    forward_growth = as_number(stock.get("forward_eps_cagr"))
    peg_limit = as_number(criteria.get("peg_max"))

    if stock.get("peg_quality") == "high_growth_base_effect":
        peg = None

    if stock.get("sector") == "growth":
        eps_fwd = as_number(stock.get("eps_fwd")) or 0
        if eps_fwd <= 0 or peg is None:
            ps = as_number(stock.get("ps"))
            band = as_number(stock.get("band_pct"))
            surprise = as_number(stock.get("earnings_surprise_pct"))
            target_gap = as_number(stock.get("target_gap"))
            checks = {
                "ps": ps is not None and ps < as_number(criteria.get("ps_max")),
                "band": band is not None and band < as_number(criteria.get("band_max")),
                "surprise": surprise is not None and surprise >= as_number(criteria.get("fallback_surprise_min")),
                "target_gap": target_gap is not None and target_gap >= as_number(criteria.get("fallback_target_gap_min")),
            }
            if all(value is not None for value in (
                criteria.get("ps_max"), criteria.get("band_max"),
                criteria.get("fallback_surprise_min"), criteria.get("fallback_target_gap_min"),
            )):
                return {"status": "pass" if all(checks.values()) else "fail", "basis": "growth_fallback", "checks": checks}

    if peg is not None and peg_limit is not None:
        if stock.get("sector") == "industrial" and criteria.get("per_max") is not None:
            per_limit = as_number(criteria.get("per_max"))
            per_value = per_ttm or per_fwd
            passed = peg < peg_limit or (per_value is not None and per_value < per_limit)
            return {
                "status": "pass" if passed else "fail",
                "basis": "peg_or_per",
                "value": peg,
                "limit": peg_limit,
            }
        return {
            "status": "pass" if peg < peg_limit else "fail",
            "basis": "peg",
            "value": peg,
            "limit": peg_limit,
        }

    if market == "kr":
        per_limit = as_number(criteria.get("kr_per_fallback_max"))
        per_source = stock.get("per_source")
        fallback_allowed = forward_growth is None or forward_growth > 0
        if per_fwd is not None and per_limit is not None and per_source in KR_FORWARD_PER_SOURCES and fallback_allowed:
            return {
                "status": "pass" if per_fwd < per_limit else "fail",
                "basis": "forward_per",
                "value": per_fwd,
                "limit": per_limit,
            }

    return {"status": "pending", "basis": "unavailable", "reason": "유효한 밸류에이션 입력 없음"}


def fundamental_gate(stock: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    hits = as_number(stock.get("selection_hits"))
    slope = as_number(stock.get("trend_slope_mom_pct"))
    slope_limit = as_number(criteria.get("slope_mom_min"))
    if slope_limit is None:
        slope_limit = -1.0
    if hits is None:
        return {"status": "pending", "reason": "보조조건 집계 없음"}
    slope_ok = slope is None or slope >= slope_limit
    passed = hits >= 2 and slope_ok
    return {
        "status": "pass" if passed else "fail",
        "selection_hits": int(hits),
        "required_hits": 2,
        "eps_revision_gate": "pass" if slope_ok else "fail",
        "eps_revision_limit": slope_limit,
    }


def clamp_rating(value: float) -> int:
    return max(1, min(100, round(value)))


def _bounded_component(value: float, scale: float) -> int:
    """0을 중립 50으로 두고 극단값의 영향은 완만하게 제한한다."""
    return clamp_rating(50 + 50 * math.tanh(value / scale))


def _weighted_rating(
    components: list[dict[str, Any]],
    minimum_available: int = 1,
    required_any: set[str] | None = None,
) -> tuple[int | None, dict[str, Any]]:
    available = [item for item in components if item.get("score") is not None]
    detail = {
        "available": len(available),
        "total": len(components),
        "components": [{"id": item["id"], "score": item["score"]} for item in available],
    }
    if len(available) < minimum_available or (required_any and not any(item["id"] in required_any for item in available)):
        detail.update({"level": "unavailable", "minimum_required": minimum_available})
        return None, detail
    weight_sum = sum(float(item["weight"]) for item in available)
    score = sum(float(item["score"]) * float(item["weight"]) for item in available) / weight_sum
    coverage = len(available) / len(components)
    level = "high" if coverage >= 0.75 else "medium" if coverage >= 0.5 else "low"
    detail["level"] = level
    return clamp_rating(score), detail


def _average_component(values: list[tuple[float | None, float]], scale: float) -> int | None:
    available = [(value, weight) for value, weight in values if value is not None]
    if not available:
        return None
    weight_sum = sum(weight for _, weight in available)
    return clamp_rating(sum(_bounded_component(value, scale) * weight for value, weight in available) / weight_sum)


def fundamental_rating(stock: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    """성장·수익성·현금창출·재무안정성·전망을 분리해 평가한다."""
    revenue = as_number(stock.get("rev_growth"))
    eps_growth = as_number(stock.get("forward_eps_cagr"))
    if eps_growth is None:
        eps_growth = as_number(stock.get("eps_growth"))
    surprise = as_number(stock.get("earnings_surprise_pct"))
    revision = as_number(stock.get("trend_slope_mom_pct"))
    operating_margin = as_number(stock.get("operating_margin"))
    roe = as_number(stock.get("return_on_equity"))
    fcf_margin = as_number(stock.get("free_cash_flow_margin"))
    debt_to_equity = as_number(stock.get("debt_to_equity"))
    balance_score = None
    if debt_to_equity is not None and debt_to_equity >= 0:
        balance_score = clamp_rating(50 + 50 * math.tanh((80 - debt_to_equity) / 100))
    components = [
        {"id": "growth", "weight": 0.30, "score": _average_component([(revenue, 0.45), (eps_growth, 0.55)], 40)},
        {"id": "profitability", "weight": 0.25, "score": _average_component([(operating_margin, 0.55), (roe, 0.45)], 18)},
        {"id": "cash_generation", "weight": 0.15, "score": _bounded_component(fcf_margin, 15) if fcf_margin is not None else None},
        {"id": "balance_sheet", "weight": 0.15, "score": balance_score},
        {"id": "outlook", "weight": 0.15, "score": _average_component([(surprise, 0.45), (revision, 0.55)], 12)},
    ]
    return _weighted_rating(components, minimum_available=3, required_any={"profitability", "cash_generation"})


def price_reflection_rating(stock: dict[str, Any], criteria: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    """밸류에이션 지표만으로 기대 반영 정도를 평가한다. 높을수록 부담이 큰 방향이다."""
    peg = as_number(stock.get("peg_fwd"))
    peg_limit = as_number(criteria.get("peg_max"))
    per_fwd = as_number(stock.get("per_fwd"))
    pbr = as_number(stock.get("pbr"))
    ps = as_number(stock.get("ps"))
    peg_quality = str(stock.get("peg_quality") or "")
    peg_warning = stock.get("metric_quality") == "warning" or any(
        str(warning).startswith("peg:") for warning in stock.get("metric_warnings", [])
    )

    peg_score = None
    if not peg_warning and peg is not None and peg_limit is not None and peg_limit > 0:
        peg_score = clamp_rating(50 + 50 * math.tanh((peg / peg_limit - 1) * 0.9))
    per_score = clamp_rating(50 + 50 * math.tanh((per_fwd - 20) / 20)) if per_fwd is not None and per_fwd > 0 else None
    pbr_score = clamp_rating(50 + 50 * math.tanh((pbr - 3) / 3)) if pbr is not None and pbr > 0 else None
    ps_score = clamp_rating(50 + 50 * math.tanh((ps - 5) / 5)) if ps is not None and ps > 0 else None
    peg_weight = 0.25 if peg_quality == "provider_reported" else 0.35
    components = [
        {"id": "peg", "weight": peg_weight, "score": peg_score},
        {"id": "forward_per", "weight": 0.35, "score": per_score},
        {"id": "pbr", "weight": 0.15, "score": pbr_score},
        {"id": "ps", "weight": 0.15, "score": ps_score},
    ]
    rating, quality = _weighted_rating(components, minimum_available=2)
    if peg_quality == "provider_reported":
        if quality["level"] == "high":
            quality["level"] = "medium"
        quality["note"] = "provider_peg_horizon_unavailable"
    return rating, quality


def normalize_stock(stock: dict[str, Any], market: str, criteria_by_sector: dict[str, Any]) -> dict[str, Any]:
    sector = str(stock.get("sector") or "")
    criteria = criteria_by_sector.get(sector, {})
    growth = stock.get("forward_eps_cagr")
    if growth is None:
        growth = stock.get("eps_growth")
    fundamentals = fundamental_gate(stock, criteria)
    valuation = valuation_gate(stock, criteria, market)
    authoritative_gate = stock.get("step1")
    reproduced_gate = fundamentals["status"] == "pass" and valuation["status"] == "pass"
    if not isinstance(authoritative_gate, bool):
        alignment = "pending"
    else:
        alignment = "aligned" if authoritative_gate == reproduced_gate else "drift"
    if alignment == "drift":
        reason = "Stock SAYO 공개 결과와 로직 계약이 달라 기준 갱신이 필요합니다"
        fundamentals = {"status": "pending", "reason": reason}
        valuation = {"status": "pending", "reason": reason}

    fundamental_score, fundamental_quality = fundamental_rating(stock)
    reflection_score, reflection_quality = price_reflection_rating(stock, criteria)
    if alignment == "drift":
        fundamental_score = None
        reflection_score = None
        fundamental_quality = {"level": "unavailable", "available": 0, "total": 5, "components": []}
        reflection_quality = {"level": "unavailable", "available": 0, "total": 4, "components": []}
    ratings = {
        "fundamental": fundamental_score,
        "price_reflection": reflection_score,
        "reference_line": 50,
        "orientation": {
            "fundamental": "higher_is_stronger",
            "price_reflection": "higher_is_more_priced_in",
        },
    }
    rating_quality = {
        "fundamental": fundamental_quality,
        "price_reflection": reflection_quality,
    }

    return {
        "ticker": str(stock.get("code") or "").upper(),
        "name": stock.get("name") or stock.get("code") or "",
        "market": market,
        "sector": sector,
        "metrics": {
            "peg_fwd": as_number(stock.get("peg_fwd")),
            "per_fwd": as_number(stock.get("per_fwd")),
            "pbr": as_number(stock.get("pbr")),
            "ps": as_number(stock.get("ps")),
            "forward_eps_growth": as_number(growth),
            "revenue_growth": as_number(stock.get("rev_growth")),
            "earnings_surprise": as_number(stock.get("earnings_surprise_pct")),
            "eps_revision_1m": as_number(stock.get("trend_slope_mom_pct")),
            "target_gap": as_number(stock.get("target_gap")),
            "band_pct": as_number(stock.get("band_pct")),
            "dividend_yield": as_number(stock.get("div_yield")),
            "peg_source": stock.get("peg_source"),
            "peg_quality": stock.get("peg_quality"),
            "eps_growth_source": stock.get("eps_growth_source"),
            "operating_margin": as_number(stock.get("operating_margin")),
            "return_on_equity": as_number(stock.get("return_on_equity")),
            "free_cash_flow_margin": as_number(stock.get("free_cash_flow_margin")),
            "debt_to_equity": as_number(stock.get("debt_to_equity")),
        },
        "fundamental_gate": fundamentals,
        "valuation_gate": valuation,
        "ratings": ratings,
        "rating_quality": rating_quality,
        "quant_gate_pass": authoritative_gate if isinstance(authoritative_gate, bool) else None,
        "sayo_alignment": alignment,
        "metric_quality": stock.get("metric_quality") or "unknown",
        "data_as_of": stock.get("data_as_of") or stock.get("stale_as_of"),
    }


def _percentile_ranks(values: list[tuple[int, int]]) -> dict[int, int]:
    """동일 값은 같은 평균 순위를 쓰는 1~100 백분위로 변환한다."""
    if len(values) < 3:
        return {}
    sorted_values = sorted(values, key=lambda item: item[1])
    ranks: dict[int, int] = {}
    for index, (stock_index, value) in enumerate(sorted_values):
        tie_indices = [position for position, (_, candidate) in enumerate(sorted_values) if candidate == value]
        average_rank = sum(tie_indices) / len(tie_indices)
        ranks[stock_index] = clamp_rating(1 + 99 * average_rank / (len(sorted_values) - 1))
    return ranks


def apply_peer_context(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """동일 시장·섹터 안에서만 상대 위치를 보조 정보로 반영한다.

    펀더멘털은 절대 체력 점수를 유지하고 상대 위치는 별도로 공개한다. 주가반영만
    절대 수치 75%와 동종업계 백분위 25%를 섞어, 한 종목의 높은 멀티플을
    동일 업종의 구조적 멀티플과 구분한다.
    """
    groups: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for index, stock in enumerate(stocks):
        groups.setdefault((stock.get("market", ""), stock.get("sector", "")), []).append((index, stock))
    for group in groups.values():
        for axis in ("fundamental", "price_reflection"):
            values = [
                (index, item["ratings"].get(axis))
                for index, item in group
                if isinstance(item["ratings"].get(axis), int)
            ]
            ranks = _percentile_ranks([(index, value) for index, value in values if value is not None])
            for index, item in group:
                rating = item["ratings"].get(axis)
                quality = item.get("rating_quality", {}).get(axis, {})
                if not isinstance(rating, int):
                    continue
                if index in ranks:
                    if axis == "price_reflection":
                        item["ratings"][axis] = clamp_rating(rating * 0.75 + ranks[index] * 0.25)
                    quality["peer_percentile"] = ranks[index]
                    quality["peer_group_size"] = len(values)
                else:
                    quality["peer_group_size"] = len(values)
                    quality["note"] = "peer_group_too_small"
                if not item.get("data_as_of"):
                    quality["as_of"] = "missing"
    return stocks


def criteria_digest(criteria_config: dict[str, Any]) -> str:
    canonical = json.dumps(criteria_config.get("criteria", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_post_earnings_adjustment(
    previous: dict[str, Any],
    refreshed: dict[str, Any],
    event: dict[str, Any],
    updated_at: datetime,
) -> dict[str, Any]:
    """실적 뒤 새 EPS 전망이 확인된 경우에만 기업 판단을 교체한다."""

    candidate = deepcopy(refreshed)
    previous_growth = as_number(previous.get("metrics", {}).get("forward_eps_growth"))
    current_growth = as_number(candidate.get("metrics", {}).get("forward_eps_growth"))
    revision = as_number(candidate.get("metrics", {}).get("eps_revision_1m"))
    growth_delta = (
        current_growth - previous_growth
        if current_growth is not None and previous_growth is not None
        else None
    )
    lowered = bool(
        (revision is not None and revision < -1)
        or (growth_delta is not None and growth_delta <= -5)
    )
    penalty = 0
    if lowered:
        penalty = 10 if (
            (revision is not None and revision <= -5)
            or (growth_delta is not None and growth_delta <= -10)
        ) else 5
        fundamental = candidate.get("ratings", {}).get("fundamental")
        burden = candidate.get("ratings", {}).get("price_reflection")
        if isinstance(fundamental, int):
            candidate["ratings"]["fundamental"] = clamp_rating(fundamental - penalty)
        if isinstance(burden, int):
            candidate["ratings"]["price_reflection"] = clamp_rating(burden + penalty)

    if lowered:
        direction = "lowered"
        label = "실적 발표 뒤 EPS 전망 하향 반영"
    elif revision is not None and revision > 1:
        direction = "raised"
        label = "실적 발표 뒤 EPS 전망 상향 확인"
    else:
        direction = "unchanged"
        label = "실적 발표 뒤 EPS 전망 유지"
    candidate["assessment"] = {
        "state": "updated_after_earnings",
        "label": label,
        "event_id": event["event_id"],
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "previous_data_as_of": previous.get("data_as_of"),
        "eps_revision_1m": revision,
        "forward_eps_growth_change_pp": (
            round(growth_delta, 1) if growth_delta is not None else None
        ),
        "consensus_direction": direction,
        "score_adjustment": penalty,
    }
    return candidate
