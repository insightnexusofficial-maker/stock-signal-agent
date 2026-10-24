from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from study_protocol import KEY_HYPOTHESES, deduplicate_evidence, evidence_lifecycle, publisher_key


KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = "1.2"
LOGIC_VERSION = "cycle-engine-v6-2026-10-24"
METHODOLOGY_VERSION = "evidence-lifecycle-publisher-review-2026-10-24"
PILLARS = {"demand", "inventory", "pricing", "supply", "earnings"}
DIRECTIONS = {"positive", "negative", "neutral"}
SEGMENTS = [
    ("01", "cloud_capex", "클라우드 설비투자"),
    ("02", "ai_compute_design", "AI 연산·칩 설계"),
    ("03", "memory_hbm_dram", "HBM·DRAM"),
    ("04", "memory_nand", "NAND"),
    ("05", "foundry_logic", "파운드리·로직"),
    ("06", "equipment_materials", "장비·소부장"),
    ("07", "analog_auto_industrial", "아날로그·자동차·산업용"),
    ("08", "power_infrastructure", "전력·데이터센터 인프라"),
    ("09", "ai_services", "AI 모델·서비스"),
]
STATUS_LABELS = {
    "favorable": "사이클 우호",
    "neutral": "사이클 중립",
    "caution": "사이클 주의",
}


def valid_evidence(item: dict[str, Any], today: date) -> bool:
    required = ("id", "segment", "pillar", "direction", "fact", "source_url", "published_at", "source_family")
    if any(not item.get(field) for field in required):
        return False
    if item.get("review_status") != "verified" or item.get("confidence") != "direct":
        return False
    if item.get("pillar") not in PILLARS or item.get("direction") not in DIRECTIONS:
        return False
    return evidence_lifecycle(item, today)["status"] == "current"


def status_for_segment(items: list[dict[str, Any]]) -> tuple[str, int, str, list[str], list[str]]:
    by_direction: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_direction[item["direction"]].append(item)

    def qualifies(direction: str) -> bool:
        selected = by_direction[direction]
        families = {publisher_key(item) for item in selected}
        pillars = {item["pillar"] for item in selected}
        return len(families) >= 2 and len(pillars) >= 2 and all(
            item.get("audit_passed") is True and item.get("audit_kind") != "deterministic"
            for item in selected
        )

    positive_ok = qualifies("positive")
    negative_ok = qualifies("negative")
    positives = by_direction["positive"]
    negatives = by_direction["negative"]
    supporting: list[dict[str, Any]] = []
    contrary: list[dict[str, Any]] = []

    if positive_ok and not negative_ok:
        status = "favorable"
        supporting, contrary = positives, negatives
        reason = "서로 다른 출처군과 증거 축에서 긍정 근거가 확인됐습니다."
    elif negative_ok and not positive_ok:
        status = "caution"
        supporting, contrary = negatives, positives
        reason = "서로 다른 출처군과 증거 축에서 부정 근거가 확인됐습니다."
    else:
        status = "neutral"
        supporting, contrary = positives, negatives
        reason = "근거가 부족하거나 상충해 중립을 유지합니다."

    confidence_items = supporting + contrary if status == "neutral" else supporting
    families = {publisher_key(item) for item in confidence_items}
    pillars = {item["pillar"] for item in confidence_items}
    if status == "neutral":
        confidence = min(65, 25 + len(families) * 8 + len(pillars) * 6)
    else:
        confidence = min(85, 45 + len(families) * 10 + len(pillars) * 8 - len(contrary) * 5)
    return status, max(0, confidence), reason, [item["id"] for item in supporting], [item["id"] for item in contrary]


def project_company_cycles(
    companies: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """검증된 영역 상태를 종목 노출도에 보수적으로 투영한다."""
    segment_by_id = {item["id"]: item for item in segments}
    projected = []
    for company in companies:
        ticker = str(company.get("ticker") or "").upper()
        exposures = company.get("exposures") or []
        valid = [
            {"segment": item.get("segment"), "weight": float(item.get("weight", 0))}
            for item in exposures
            if item.get("segment") in segment_by_id and float(item.get("weight", 0)) > 0
        ]
        if not ticker or not valid:
            continue
        weight_sum = sum(item["weight"] for item in valid)
        if abs(weight_sum - 1.0) > 0.01:
            raise ValueError(f"{ticker}: 사이클 노출도 합계는 1이어야 합니다")
        primary = max(valid, key=lambda item: item["weight"])
        primary_segment = segment_by_id[primary["segment"]]
        non_neutral = {
            segment_by_id[item["segment"]]["status"]
            for item in valid
            if segment_by_id[item["segment"]]["status"] != "neutral"
        }
        conflicting = len(non_neutral) > 1
        primary_status = primary_segment["status"]
        if conflicting or primary_status == "neutral" or primary["weight"] < 0.60:
            status = "neutral"
        else:
            status = primary_status
        confidence = round(sum(
            segment_by_id[item["segment"]]["confidence"] * item["weight"]
            for item in valid
        ))
        if status == "neutral":
            confidence = min(65, confidence)
        if conflicting:
            reason = "연결된 산업 영역의 상태가 엇갈려 중립으로 봅니다."
        elif primary_status == "neutral":
            reason = f"주요 영역인 {primary_segment['label']}의 근거가 부족하거나 상충합니다."
        elif primary["weight"] < 0.60:
            reason = "여러 산업 영역에 걸쳐 있어 한 영역의 상태로 일반화하지 않습니다."
        else:
            reason = f"주요 영역인 {primary_segment['label']}의 검증 상태를 반영했습니다."
        projected.append({
            "ticker": ticker,
            "name": company.get("name") or ticker,
            "status": status,
            "label": STATUS_LABELS[status],
            "confidence": max(0, min(85, confidence)),
            "primary_segment": primary["segment"],
            "reason": reason,
            "exposures": valid,
            "assessment_status": "conflicting" if conflicting else primary_segment.get("assessment_status", "insufficient"),
            "data_status": primary_segment.get("data_status", "unavailable"),
            "key_hypotheses": primary_segment.get("key_hypotheses", []),
            "supporting_evidence_ids": primary_segment.get("supporting_evidence_ids", []),
            "contrary_evidence_ids": primary_segment.get("contrary_evidence_ids", []),
            "context_evidence_ids": primary_segment.get("context_evidence_ids", []),
            "last_reviewed_at": primary_segment.get("last_reviewed_at"),
            "next_check_at": primary_segment.get("next_check_at"),
            "changes": primary_segment.get("changes", []),
        })
    return projected


def build_report(
    evidence: list[dict[str, Any]],
    now: datetime | None = None,
    companies: list[dict[str, Any]] | None = None,
    company_map_version: str | None = None,
    update_context: dict[str, Any] | None = None,
    previous_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(KST)
    today = now.date()
    valid = deduplicate_evidence([item for item in evidence if valid_evidence(item, today)])
    evidence_hash = hashlib.sha256(
        json.dumps(valid, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    segments = []
    previous_segments = {item["id"]: item for item in (previous_report or {}).get("segments", [])}
    for order, segment_id, label in SEGMENTS:
        selected = [item for item in valid if item["segment"] == segment_id]
        status, confidence, reason, supporting, contrary = status_for_segment(selected)
        publishers = {publisher_key(item) for item in selected}
        pillars = {item["pillar"] for item in selected}
        directions = {item["direction"] for item in selected}
        audited = bool(selected) and all(item.get("audit_passed") is True and item.get("audit_kind") != "deterministic" for item in selected)
        assessment = (
            "sufficient" if status != "neutral" else
            "conflicting" if {"positive", "negative"}.issubset(directions) else
            "awaiting_independent_review" if selected and not audited else "insufficient"
        )
        expired_count = sum(item.get("segment") == segment_id and not valid_evidence(item, today) for item in evidence)
        reviews = [str(item.get("last_reviewed_at")) for item in selected if item.get("last_reviewed_at")]
        next_release = sorted(str(item["next_expected_release_at"]) for item in selected if item.get("next_expected_release_at") and str(item["next_expected_release_at"])[:10] >= today.isoformat())
        old = previous_segments.get(segment_id)
        changes = []
        if old and (old.get("status") != status or old.get("assessment_status") != assessment):
            changes.append({"from": old.get("status"), "to": status, "assessment_from": old.get("assessment_status"), "assessment_to": assessment})
        segments.append({
            "order": order,
            "id": segment_id,
            "label": label,
            "status": status,
            "confidence": confidence,
            "horizon_months": "3-6",
            "reason": reason,
            "supporting_evidence_ids": supporting,
            "contrary_evidence_ids": contrary,
            "context_evidence_ids": [item["id"] for item in selected if item["direction"] == "neutral"],
            "assessment_status": assessment,
            "data_status": "current" if selected else "expired" if expired_count else "unavailable",
            "coverage": {"publisher_count": len(publishers), "pillar_count": len(pillars), "evidence_count": len(selected), "expired_or_invalid_count": expired_count},
            "key_hypotheses": KEY_HYPOTHESES.get(segment_id, []),
            "last_reviewed_at": max(reviews) if reviews else None,
            "next_check_at": next_release[0] if next_release else (now + timedelta(days=7)).isoformat(timespec="seconds"),
            "changes": changes,
        })
    context = update_context or {
        "type": "weekly",
        "critical": False,
        "event_ids": [],
        "status_changes": [],
        "reason": "토요일 정기 전체 조사",
    }
    suffix = "event" if context.get("type") == "event_interrupt" else "weekly"
    report_id = f"semiconductor-cycle-{now.strftime('%Y-%m-%d-%H%M')}-{suffix}"
    quality_gate = {
        "status": "passed" if valid else "insufficient",
        "mode": "verified-only",
        "evidence_count": len(valid),
        "message": "검증된 공식 근거만 사이클 상태에 반영합니다.",
    }
    if not valid:
        quality_gate["message"] = "검증된 최신 근거가 없어 모든 세그먼트를 중립으로 유지합니다."
    return {
        "schema_version": SCHEMA_VERSION,
        "logic_version": LOGIC_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "evidence_sha256": evidence_hash,
        "report_id": report_id,
        "generated_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=8)).isoformat(timespec="seconds"),
        "fallback_status": "pending-neutral",
        "update_context": context,
        "quality_gate": quality_gate,
        "segments": segments,
        "company_map_version": company_map_version,
        "company_cycle": project_company_cycles(companies or [], segments),
        "evidence": valid,
        "collection_coverage": (previous_report or {}).get("collection_coverage", {"last_attempt_at": None, "sources": []}),
        "assessment_policy": {"frequency_aware": True, "publisher_identity": "canonical-origin", "independent_review_required": True, "next_full_check_at": (now + timedelta(days=7)).isoformat(timespec="seconds")},
    }
