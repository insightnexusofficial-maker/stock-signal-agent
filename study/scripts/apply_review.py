#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from cycle_engine import DIRECTIONS, PILLARS, SEGMENTS


ROOT = Path(__file__).resolve().parents[1]


def atomic_write(path: Path, payload: dict) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="검토 완료 JSON을 검증된 evidence 저장소에 반영")
    parser.add_argument("review_file", type=Path)
    args = parser.parse_args()
    reviews = json.loads(args.review_file.read_text(encoding="utf-8")).get("reviews", [])
    inbox = json.loads((ROOT / "data/inbox.json").read_text(encoding="utf-8"))
    candidates = {item["id"]: item for item in inbox.get("candidates", [])}
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    source_by_id = {item["id"]: item for item in registry["sources"]}
    valid_segments = {segment_id for _, segment_id, _ in SEGMENTS}
    evidence_path = ROOT / "data/evidence.json"
    evidence_document = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence_by_id = {item["id"]: item for item in evidence_document.get("evidence", [])}

    for review in reviews:
        candidate = candidates.get(review.get("candidate_id"))
        if not candidate:
            raise ValueError(f"수집 후보에 없는 ID: {review.get('candidate_id')}")
        source = source_by_id[candidate["source_id"]]
        segment = review.get("segment")
        if segment not in valid_segments or segment not in source["segments"]:
            raise ValueError(f"출처에 허용되지 않은 세그먼트: {segment}")
        if review.get("pillar") not in PILLARS or review.get("direction") not in DIRECTIONS:
            raise ValueError("잘못된 증거 축 또는 방향")
        if review.get("review_status") != "verified" or review.get("confidence") != "direct":
            raise ValueError("검증 완료·직접 근거만 evidence에 반영할 수 있습니다")
        if candidate.get("published_at_quality") != "direct" or review.get("published_at") != candidate.get("published_at"):
            raise ValueError("수집기가 직접 확인한 발행일과 일치하지 않습니다")
        if not 12 <= len(str(review.get("fact") or "")) <= 500:
            raise ValueError("근거 사실 문장 길이가 허용 범위를 벗어났습니다")
        if urlparse(candidate["source_url"]).hostname != source["allowed_domain"]:
            raise ValueError("출처 도메인 검증 실패")
        evidence_id = f"{candidate['id']}-{segment}"
        evidence_by_id[evidence_id] = {
            "id": evidence_id,
            "source_id": candidate["source_id"],
            "segment": segment,
            "pillar": review["pillar"],
            "direction": review["direction"],
            "fact": review["fact"],
            "source_url": candidate["source_url"],
            "published_at": review["published_at"],
            "max_age_days": int(source.get("max_age_days", 45)),
            "source_family": candidate["source_family"],
            "confidence": "direct",
            "review_status": "verified",
            "audit_passed": review.get("audit_passed") is True,
            "contrary": review.get("contrary") is True,
            "reviewed_by": review.get("reviewed_by") or "human",
            "audited_by": review.get("audited_by"),
            "audit_reason": review.get("audit_reason"),
        }

    evidence_document["evidence"] = sorted(evidence_by_id.values(), key=lambda item: item["id"])
    atomic_write(evidence_path, evidence_document)
    print(f"검증 근거 {len(evidence_document['evidence'])}개 저장")


if __name__ == "__main__":
    main()
