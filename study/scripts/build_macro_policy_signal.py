#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
INPUT = ROOT / "data" / "macro-policy-signals.json"
OUTPUT = ROOT / "public" / "data" / "macro-policy-latest.json"

ALLOWED_DOMAINS = {
    "www.federalreserve.gov",
    "www.investing.com",
    "apnews.com",
    "www.reuters.com",
    "www.cmegroup.com",
}
ACTIVE_STATUSES = {"hike_watch", "hike_likely", "hold_watch", "cut_watch", "neutral"}
TONES = {"good", "warn", "bad", "neutral"}


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_signal(signal: dict, now: datetime) -> list[str]:
    errors: list[str] = []
    evidence = signal.get("evidence")
    expires_at = None
    try:
        expires_at = _parse_dt(signal["expires_at"])
    except (KeyError, ValueError, TypeError):
        errors.append("expires_at 형식 오류")
    if expires_at and expires_at <= now:
        errors.append("이미 만료된 신호")
    if signal.get("status") not in ACTIVE_STATUSES:
        errors.append("상태 오류")
    if signal.get("direction") not in {"hike", "hold", "cut", "mixed"}:
        errors.append("방향 오류")
    if signal.get("probability_level") not in {"low", "watch", "elevated", "high"}:
        errors.append("가능성 등급 오류")
    probability = signal.get("probability_pct")
    if probability is not None and not 0 <= float(probability) <= 100:
        errors.append("가능성 수치 범위 오류")
    if not 0 <= int(signal.get("confidence", -1)) <= 85:
        errors.append("신뢰도 범위 오류")
    if signal.get("chip_tone") not in TONES:
        errors.append("칩 tone 오류")
    if not isinstance(evidence, list) or len(evidence) < 2:
        errors.append("독립 근거 부족")
    else:
        families = {item.get("source_family") for item in evidence}
        if len(families) < 2:
            errors.append("독립 출처군 부족")
        for item in evidence:
            host = urlparse(str(item.get("source_url") or "")).hostname
            if host not in ALLOWED_DOMAINS:
                errors.append(f"허용되지 않은 도메인 {host}")
            try:
                datetime.fromisoformat(str(item["published_at"]))
            except (KeyError, ValueError, TypeError):
                errors.append(f"근거 발행일 오류 {item.get('id')}")
            if not item.get("fact"):
                errors.append(f"근거 사실 누락 {item.get('id')}")
    return errors


def build(now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    source = json.loads(INPUT.read_text(encoding="utf-8"))
    signals = source.get("signals") or []
    if source.get("schema_version") != "1.0" or not signals:
        raise RuntimeError("macro-policy-signals.json 계약 오류")

    candidates = sorted(
        signals,
        key=lambda item: (str(item.get("source_published_at") or ""), str(item.get("id") or "")),
        reverse=True,
    )
    selected = candidates[0]
    errors = _validate_signal(selected, now)
    if errors:
        raise RuntimeError("macro policy signal 검증 실패: " + "; ".join(errors))

    public_core = {
        "signal_id": selected["id"],
        "sector": {
            "id": selected["sector_id"],
            "label": selected["sector_label"],
            "status": selected["status"],
            "direction": selected["direction"],
            "probability_level": selected["probability_level"],
            "probability_pct": selected.get("probability_pct"),
            "confidence": selected["confidence"],
            "display_label": selected["display_label"],
            "chip_tone": selected["chip_tone"],
            "horizon": selected["horizon"],
            "summary": selected["summary"],
            "primary_source_name": selected["primary_source_name"],
            "primary_source_url": selected["primary_source_url"],
            "source_published_at": selected["source_published_at"],
            "review_status": "verified",
            "evidence_ids": [item["id"] for item in selected["evidence"]],
        },
        "evidence": selected["evidence"],
    }
    content_sha256 = _canonical_sha256(public_core)
    return {
        "schema_version": "1.0",
        "logic_version": "macro-policy-v1-2026-08-29",
        "generated_at": now.isoformat(timespec="seconds"),
        "expires_at": selected["expires_at"],
        "content_sha256": content_sha256,
        "quality_gate": {
            "status": "passed",
            "mode": "official-speech-plus-market-pricing",
            "evidence_count": len(selected["evidence"]),
            "message": "공식 발언과 독립 시장 반응 근거가 함께 확인된 거시정책 신호만 표시합니다."
        },
        **public_core,
    }


def main() -> None:
    payload = build()
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"거시정책 신호 {payload['signal_id']} → {OUTPUT}")


if __name__ == "__main__":
    main()
