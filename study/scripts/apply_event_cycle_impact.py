#!/usr/bin/env python3
"""검증된 발표 결과를 사이클 근거로 승격하고 필요할 때만 중간 발행한다."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from cycle_engine import KST, build_report
from event_feed import validate_calendar, validate_results


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = ROOT / "data" / "event-calendar.json"
RESULTS_PATH = ROOT / "data" / "event-results.json"
EVIDENCE_PATH = ROOT / "data" / "evidence.json"
COMPANY_MAP_PATH = ROOT / "data" / "company-cycle-map.json"
CYCLE_PATH = ROOT / "public" / "data" / "cycle-latest.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _pillar(metric: str) -> str:
    metric = metric.lower()
    if any(token in metric for token in ("inventory", "재고")):
        return "inventory"
    if any(token in metric for token in ("price", "asp", "pricing", "가격")):
        return "pricing"
    if any(token in metric for token in ("capex", "capacity", "supply", "출하", "공급")):
        return "supply"
    if any(token in metric for token in ("order", "backlog", "rpo", "cloud", "azure", "demand", "수요")):
        return "demand"
    return "earnings"


def result_evidence(result: dict, event: dict) -> list[dict]:
    if (
        event.get("kind") != "earnings"
        or result.get("status") != "complete"
        or result.get("review_status") != "verified"
        or result.get("impact_review", {}).get("audit_passed") is not True
    ):
        return []
    source_urls = result.get("source_urls") or []
    if not source_urls:
        return []
    ticker = str(event.get("ticker") or "company").lower()
    published = str(
        result.get("source_published_at") or result.get("retrieved_at")
    )[:10]
    candidates = []
    for fact in result.get("facts") or []:
        try:
            value = float(fact.get("value"))
        except (TypeError, ValueError):
            continue
        if fact.get("unit") != "percent" or abs(value) < 5:
            continue
        metric = str(fact.get("metric") or "metric")
        direction = "positive" if value > 0 else "negative"
        label = str(fact.get("label") or metric.replace("_", " "))
        for segment in event.get("segments") or []:
            digest = hashlib.sha256(
                f"{result['event_id']}|{segment}|{metric}".encode("utf-8")
            ).hexdigest()[:12]
            candidates.append({
                "id": f"event-{digest}",
                "source_id": result["event_id"],
                "segment": segment,
                "pillar": _pillar(metric),
                "direction": direction,
                "fact": f"{event['name']}: {label}은 공식 비교 기준 {value:+g}%였다.",
                "source_url": source_urls[0],
                "published_at": published,
                "max_age_days": 30,
                "source_family": f"event_{ticker}_ir",
                "confidence": "direct",
                "review_status": "verified",
                "audit_passed": True,
                "contrary": direction == "negative",
                "reviewed_by": "official-event-impact-review",
                "audited_by": "deterministic-generalization-guard",
                "origin_event_id": result["event_id"],
            })
    return candidates


def _segment_changes(previous: dict, candidate: dict) -> list[dict]:
    before = {item["id"]: item for item in previous.get("segments") or []}
    changes = []
    for current in candidate.get("segments") or []:
        old = before.get(current["id"], {})
        status_changed = old.get("status") != current.get("status")
        confidence_delta = int(current.get("confidence") or 0) - int(old.get("confidence") or 0)
        if status_changed or abs(confidence_delta) >= 8:
            changes.append({
                "segment": current["id"],
                "label": current["label"],
                "from": old.get("status", "neutral"),
                "to": current["status"],
                "confidence_delta": confidence_delta,
            })
    return changes


def apply(now: datetime | None = None) -> dict:
    now = (now or datetime.now(KST)).astimezone(KST)
    calendar = validate_calendar(_read(CALENDAR_PATH))
    results = validate_results(_read(RESULTS_PATH), calendar)
    evidence_document = _read(EVIDENCE_PATH)
    company_map = _read(COMPANY_MAP_PATH)
    previous = _read(CYCLE_PATH)
    event_by_id = {item["id"]: item for item in calendar.get("events") or []}
    existing_ids = {item["id"] for item in evidence_document.get("evidence") or []}
    result_hashes = evidence_document.setdefault("event_result_hashes", {})
    hashes_changed = False
    new_evidence = []
    event_ids = []
    critical_reasons = []
    for result in results.get("results") or []:
        event = event_by_id.get(result["event_id"])
        if not event or result.get("status") != "complete" or result.get("review_status") != "verified":
            continue
        result_hash = hashlib.sha256(json.dumps(
            {
                "facts": result.get("facts") or [],
                "impact_review": result.get("impact_review"),
                "shock": result.get("shock"),
                "source_urls": result.get("source_urls") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")).hexdigest()
        if result_hashes.get(result["event_id"]) == result_hash:
            continue
        result_hashes[result["event_id"]] = result_hash
        hashes_changed = True
        candidates = [
            item for item in result_evidence(result, event)
            if item["id"] not in existing_ids
        ]
        if candidates:
            new_evidence.extend(candidates)
            existing_ids.update(item["id"] for item in candidates)
            event_ids.append(result["event_id"])
        shock = result.get("shock") or {}
        impact = result.get("impact_review") or {}
        if shock.get("is_shock") is True and shock.get("audit_passed") is True:
            critical_reasons.append(str(shock.get("reason") or result["summary"]))
            event_ids.append(result["event_id"])
        elif impact.get("risk_level") == "high" and impact.get("trend_change") == "weakening":
            critical_reasons.append(str(impact.get("risk_summary") or result["summary"]))
            event_ids.append(result["event_id"])

    if new_evidence or hashes_changed:
        evidence_document.setdefault("evidence", []).extend(new_evidence)
        evidence_document["evidence"].sort(key=lambda item: (item["published_at"], item["id"]))
        evidence_document["version"] = "1.2"
        _write(EVIDENCE_PATH, evidence_document)

    unique_event_ids = list(dict.fromkeys(event_ids))
    candidate = build_report(
        evidence_document.get("evidence", []),
        now=now,
        companies=company_map.get("companies", []),
        company_map_version=company_map.get("version"),
        update_context={
            "type": "event_interrupt",
            "critical": bool(critical_reasons),
            "event_ids": unique_event_ids,
            "status_changes": [],
            "reason": "공식 발표 결과에 따른 중간 사이클 재평가",
        },
    )
    changes = _segment_changes(previous, candidate)
    critical = bool(critical_reasons) or any(
        item["to"] == "caution"
        or (item["from"] == "favorable" and item["to"] == "neutral")
        for item in changes
    )
    material = bool(unique_event_ids) and (bool(changes) or critical)
    if material:
        candidate["update_context"] = {
            "type": "event_interrupt",
            "critical": critical,
            "event_ids": unique_event_ids,
            "status_changes": changes,
            "reason": (
                " · ".join(critical_reasons)[:500]
                if critical_reasons
                else "공식 발표 근거로 사이클 상태 또는 확신도가 유의미하게 변했다."
            ),
        }
        _write(CYCLE_PATH, candidate)
    return {
        "evidence_added": len(new_evidence),
        "cycle_changed": material,
        "critical": critical and material,
        "event_ids": unique_event_ids,
        "status_changes": changes,
    }


def main() -> None:
    outcome = apply()
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"cycle_changed={'true' if outcome['cycle_changed'] else 'false'}\n")
            handle.write(f"critical={'true' if outcome['critical'] else 'false'}\n")
            handle.write(f"event_ids={','.join(outcome['event_ids'])}\n")
    print(
        f"이벤트 근거 {outcome['evidence_added']}건 · "
        f"중간 사이클 발행 {'예' if outcome['cycle_changed'] else '아니오'} · "
        f"크리티컬 {'예' if outcome['critical'] else '아니오'}"
    )


if __name__ == "__main__":
    main()
