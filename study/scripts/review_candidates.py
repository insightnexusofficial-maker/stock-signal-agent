#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
API_URL = "https://api.openai.com/v1/responses"
TERRA_MODEL = os.getenv("OPENAI_TERRA_MODEL", "gpt-5.6-terra")
SOL_MODEL = os.getenv("OPENAI_SOL_MODEL", "gpt-5.6-sol")
SEGMENTS = [
    "cloud_capex", "ai_compute_design", "memory_hbm_dram", "memory_nand",
    "foundry_logic", "equipment_materials", "analog_auto_industrial",
    "power_infrastructure", "ai_services",
]
PILLARS = ["demand", "inventory", "pricing", "supply", "earnings"]
DIRECTIONS = ["positive", "negative", "neutral"]


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def output_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("Responses API에 output_text가 없습니다")


def call_responses(api_key: str, model: str, effort: str, name: str, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "reasoning": {"effort": effort},
        "input": [
            {"role": "developer", "content": (
                "제공된 공식 출처 후보 밖의 사실을 만들지 마라. 투자 추천·목표가격·매수/매도 의견을 생성하지 마라. "
                "증거가 불충분하거나 충돌하면 검증 불가를 선택하고 모든 판단을 candidate_id에 연결하라."
            )},
            {"role": "user", "content": prompt},
        ],
        "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(output_text(json.load(response)))


def extraction_schema() -> dict[str, Any]:
    review = {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_id", "segment", "pillar", "direction", "fact", "confidence_score", "should_publish", "contrary"],
        "properties": {
            "candidate_id": {"type": "string"},
            "segment": {"type": "string", "enum": SEGMENTS},
            "pillar": {"type": "string", "enum": PILLARS},
            "direction": {"type": "string", "enum": DIRECTIONS},
            "fact": {"type": "string", "maxLength": 500},
            "confidence_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "should_publish": {"type": "boolean"},
            "contrary": {"type": "boolean"},
        },
    }
    return {
        "type": "object", "additionalProperties": False, "required": ["reviews"],
        "properties": {"reviews": {"type": "array", "items": review, "maxItems": 40}},
    }


def audit_schema() -> dict[str, Any]:
    audit = {
        "type": "object", "additionalProperties": False,
        "required": ["candidate_id", "segment", "audit_passed", "reason", "generalization_warning"],
        "properties": {
            "candidate_id": {"type": "string"},
            "segment": {"type": "string", "enum": SEGMENTS},
            "audit_passed": {"type": "boolean"},
            "reason": {"type": "string", "maxLength": 300},
            "generalization_warning": {"type": "boolean"},
        },
    }
    return {
        "type": "object", "additionalProperties": False, "required": ["audits"],
        "properties": {"audits": {"type": "array", "items": audit, "maxItems": 40}},
    }


def candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["id"],
        "title": candidate.get("title", "")[:300],
        "published_at": candidate.get("published_at"),
        "source_family": candidate.get("source_family"),
        "allowed_segments": candidate.get("candidate_segments", []),
        "suggested_pillar": candidate.get("candidate_pillar"),
        "fact_candidate": candidate.get("fact_candidate"),
        "content_excerpt": candidate.get("content_excerpt", "")[:3500],
    }


def eligible_candidates(inbox: dict[str, Any], log: dict[str, Any], today: date) -> list[dict[str, Any]]:
    reviewed = {(item.get("candidate_id"), item.get("content_hash")) for item in log.get("items", [])}
    eligible = []
    for item in inbox.get("candidates", []):
        if item.get("published_at_quality") != "direct" or not item.get("published_at"):
            continue
        try:
            age = (today - date.fromisoformat(item["published_at"])).days
        except ValueError:
            continue
        if not 0 <= age <= 30 or not (item.get("content_excerpt") or item.get("fact_candidate")):
            continue
        if (item.get("id"), item.get("content_hash")) not in reviewed:
            eligible.append(item)
    return eligible[:20]


def needs_sol_audit(review: dict[str, Any], existing_evidence: list[dict[str, Any]]) -> tuple[bool, str]:
    if review.get("confidence_score", 0) >= 70:
        return True, "confidence>=70"
    directions = {item.get("direction") for item in existing_evidence if item.get("segment") == review.get("segment")}
    if ({"positive", "negative"} & directions) and review.get("direction") not in directions:
        return True, "conflicting_evidence"
    return False, "terra_only"


def main() -> None:
    parser = argparse.ArgumentParser(description="Terra 추출과 조건부 Sol 감사를 수행해 검토 JSON 생성")
    parser.add_argument("--output", type=Path, default=ROOT / "data/ai-reviewed.json")
    parser.add_argument("--log", type=Path, default=ROOT / "data/ai-review-log.json")
    args = parser.parse_args()
    now = datetime.now(KST)
    inbox = json.loads((ROOT / "data/inbox.json").read_text(encoding="utf-8"))
    evidence = json.loads((ROOT / "data/evidence.json").read_text(encoding="utf-8")).get("evidence", [])
    log = json.loads(args.log.read_text(encoding="utf-8")) if args.log.exists() else {"items": []}
    candidates = eligible_candidates(inbox, log, now.date())
    api_key = os.getenv("OPENAI_API_KEY")
    if not candidates or not api_key:
        atomic_write(args.output, {"generated_at": now.isoformat(timespec="seconds"), "reviews": [], "status": "no_new_candidates" if not candidates else "api_key_missing"})
        print("AI 검토 생략: " + ("신규 직접 근거 없음" if not candidates else "OPENAI_API_KEY 없음"))
        return

    extraction = call_responses(
        api_key, TERRA_MODEL, "low", "semiconductor_evidence_extraction", extraction_schema(),
        "아래 구조화 후보에서 직접 확인 가능한 사실만 추출하라. allowed_segments 밖으로 확장하지 마라. "
        "전망과 실현 수치를 구분하고 불충분하면 should_publish=false로 두라.\n" +
        json.dumps([candidate_payload(item) for item in candidates], ensure_ascii=False),
    )
    candidate_by_id = {item["id"]: item for item in candidates}
    proposed = []
    audit_input = []
    reasons: dict[tuple[str, str], str] = {}
    for review in extraction.get("reviews", []):
        candidate = candidate_by_id.get(review.get("candidate_id"))
        if not candidate or review.get("segment") not in candidate.get("candidate_segments", []):
            continue
        if not review.get("should_publish") or review.get("confidence_score", 0) < 70:
            continue
        needs_audit, reason = needs_sol_audit(review, evidence)
        key = (review["candidate_id"], review["segment"])
        reasons[key] = reason
        proposed.append(review)
        if needs_audit:
            audit_input.append(review)

    audits: dict[tuple[str, str], dict[str, Any]] = {}
    if audit_input:
        audited = call_responses(
            api_key, SOL_MODEL, "high", "semiconductor_evidence_audit", audit_schema(),
            "아래 Terra 추출을 반대 의견 관점에서 감사하라. 단일 제품 일반화, 전망/실현 혼동, 상반 근거 누락, "
            "원문 후보로 뒷받침되지 않는 표현이 있으면 audit_passed=false로 두라.\n" +
            json.dumps(audit_input, ensure_ascii=False),
        )
        audits = {(item["candidate_id"], item["segment"]): item for item in audited.get("audits", [])}

    reviews = []
    log_items = log.get("items", [])
    for review in proposed:
        candidate = candidate_by_id[review["candidate_id"]]
        key = (review["candidate_id"], review["segment"])
        audit = audits.get(key, {})
        reviews.append({
            "candidate_id": review["candidate_id"], "segment": review["segment"],
            "pillar": review["pillar"], "direction": review["direction"], "fact": review["fact"],
            "published_at": candidate["published_at"], "confidence": "direct", "review_status": "verified",
            "audit_passed": audit.get("audit_passed") is True and not audit.get("generalization_warning", False),
            "contrary": review["contrary"], "reviewed_by": TERRA_MODEL, "audited_by": SOL_MODEL if key in audits else None,
            "sol_reason": reasons.get(key), "audit_reason": audit.get("reason"),
        })
        log_items.append({
            "candidate_id": candidate["id"], "content_hash": candidate.get("content_hash"),
            "reviewed_at": now.isoformat(timespec="seconds"), "terra_model": TERRA_MODEL,
            "sol_model": SOL_MODEL if key in audits else None,
        })
    atomic_write(args.output, {"generated_at": now.isoformat(timespec="seconds"), "reviews": reviews, "status": "reviewed"})
    atomic_write(args.log, {"items": log_items[-500:]})
    print(f"AI 검토 {len(reviews)}개 · Sol 감사 {len(audits)}개 → {args.output}")


if __name__ == "__main__":
    main()
