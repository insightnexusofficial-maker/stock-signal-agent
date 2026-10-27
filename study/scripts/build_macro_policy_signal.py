#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from official_policy import refresh_official_policy


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
INPUT = ROOT / "data" / "macro-policy-signals.json"
OUTPUT = ROOT / "public" / "data" / "macro-policy-latest.json"
RESULTS = ROOT / "data" / "event-results.json"
CALENDAR = ROOT / "data" / "event-calendar.json"

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
    try:
        if probability is not None and (not math.isfinite(float(probability)) or not 0 <= float(probability) <= 100):
            errors.append("가능성 수치 범위 오류")
    except (ValueError, TypeError):
        errors.append("가능성 수치 형식 오류")
    try:
        if not 0 <= int(signal.get("confidence", -1)) <= 85:
            errors.append("신뢰도 범위 오류")
    except (ValueError, TypeError):
        errors.append("신뢰도 형식 오류")
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
                published = _parse_dt(item["published_at"])
                if not published <= now <= published + timedelta(days=120):
                    errors.append("근거 발행일 미래 또는 수명 초과")
            except (KeyError, ValueError, TypeError):
                errors.append(f"근거 발행일 오류 {item.get('id')}")
            if not item.get("fact"):
                errors.append(f"근거 사실 누락 {item.get('id')}")
    return errors


def official_policy_context(results: dict, calendar: dict, now: datetime) -> dict | None:
    candidates = [item for item in results.get("results", []) if str(item.get("event_id", "")).startswith("macro-fomc-") and item.get("status") == "complete" and item.get("review_status") == "verified"]
    for item in sorted(candidates, key=lambda row: str(row.get("source_published_at") or ""), reverse=True):
        try:
            published = _parse_dt(item["source_published_at"])
        except (KeyError, TypeError, ValueError):
            continue
        urls = [url for url in item.get("source_urls", []) if urlparse(str(url)).hostname == "www.federalreserve.gov"]
        facts = {fact.get("metric"): fact.get("value") for fact in item.get("facts", [])}
        lower = facts.get("federal_funds_target_range_lower")
        upper = facts.get("federal_funds_target_range_upper")
        try:
            lower, upper = float(lower), float(upper)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (lower, upper)) or not 0 <= lower <= upper <= 100:
            continue
        if not urls or not published <= now <= published + timedelta(days=62):
            continue
        expiry = published + timedelta(days=62)
        for event in calendar.get("events", []):
            if not str(event.get("id", "")).startswith("macro-fomc-") or not event.get("scheduled_at"):
                continue
            scheduled = _parse_dt(event["scheduled_at"])
            if published < scheduled:
                expiry = min(expiry, scheduled + timedelta(hours=6))
        if expiry <= now:
            continue
        change = facts.get("federal_funds_target_range_change")
        direction = "mixed" if change is None else "hike" if float(change) > 0 else "cut" if float(change) < 0 else "hold"
        return {
            "signal_id": "official-decision-" + item["event_id"],
            "generated_at": now.isoformat(timespec="seconds"),
            "expires_at": expiry.isoformat(timespec="seconds"),
            "quality_gate": {"status": "passed", "mode": "official-policy-decision", "evidence_count": 1, "message": "공식 결정 사실입니다. 미래 정책 확률을 추정하지 않습니다."},
            "sector": {"id": "us_rate_policy", "label": "미국 금리정책", "status": "neutral", "direction": direction, "probability_level": "low", "probability_pct": None, "confidence": 85, "display_label": f"공식 정책금리 {float(lower):g}–{float(upper):g}%", "chip_tone": "neutral", "horizon": "최근 공식 결정", "summary": f"최근 공식 목표금리 범위는 {float(lower):g}–{float(upper):g}%입니다. 미래 인상·인하 가능성은 별도 관측 전까지 표시하지 않습니다.", "primary_source_name": "Federal Reserve", "primary_source_url": urls[0], "source_published_at": published.isoformat(timespec="seconds"), "review_status": "verified", "evidence_ids": [item["event_id"]], "target_range_lower": lower, "target_range_upper": upper},
            "evidence": [{"id": item["event_id"], "source_family": "federal_reserve_policy_decision", "source_name": "Federal Reserve", "source_url": urls[0], "published_at": published.isoformat(timespec="seconds"), "fact": f"공식 목표금리 범위 {float(lower):g}–{float(upper):g}%"}],
        }
    return None


def unavailable_context(now: datetime) -> dict:
    return {
        "signal_id": None,
        "generated_at": now.isoformat(timespec="seconds"),
        "expires_at": now.isoformat(timespec="seconds"),
        "quality_gate": {"status": "unavailable", "mode": "official-policy-decision", "evidence_count": 0, "message": "유효한 최신 정책 근거 확인 대기"},
        "sector": {"id": "us_rate_policy", "label": "미국 금리정책", "status": "neutral", "direction": "mixed", "probability_level": "low", "probability_pct": None, "confidence": 0, "display_label": "정책 자료 확인 대기", "chip_tone": "neutral", "horizon": "", "summary": "정책 자료가 없거나 만료됐습니다. 위험 해소나 중립 전망을 뜻하지 않습니다.", "primary_source_name": "Federal Reserve", "primary_source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "source_published_at": None, "review_status": "unavailable", "evidence_ids": []},
        "evidence": [],
    }


def _finish(core: dict) -> dict:
    content = {key: core[key] for key in ("signal_id", "sector", "evidence")}
    return {"schema_version": "1.0", "logic_version": "macro-policy-v2-2026-10-24", "content_sha256": _canonical_sha256(content), **core}


def build(now: datetime | None = None, *, refresh_official=False, fetcher=None, previous=None) -> dict:
    now = now or datetime.now(KST)
    source = json.loads(INPUT.read_text(encoding="utf-8"))
    signals = source.get("signals") or []
    if source.get("schema_version") != "1.0":
        raise RuntimeError("macro-policy-signals.json 계약 오류")

    candidates = sorted(
        signals,
        key=lambda item: (str(item.get("source_published_at") or ""), str(item.get("id") or "")),
        reverse=True,
    )
    selected = next((candidate for candidate in candidates if not _validate_signal(candidate, now)), None)
    if selected is None:
        if refresh_official:
            observed = refresh_official_policy(previous, now, fetcher)
            if observed.get("quality_gate", {}).get("status") == "passed":
                return _finish(observed)
        results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
        calendar = json.loads(CALENDAR.read_text()) if CALENDAR.exists() else {}
        fallback = official_policy_context(results, calendar, now)
        if refresh_official and not fallback:
            return _finish(observed)
        return _finish(fallback or unavailable_context(now))

    # A probability without the underlying pricing observation is not a number
    # the service can substantiate. Keep the qualitative reviewed context only.
    pricing = selected.get("probability_source") or {}
    probability = selected.get("probability_pct") if all(pricing.get(key) for key in ("source_url", "observed_at", "meeting", "method")) else None

    public_core = {
        "signal_id": selected["id"],
        "sector": {
            "id": selected["sector_id"],
            "label": selected["sector_label"],
            "status": selected["status"],
            "direction": selected["direction"],
            "probability_level": selected["probability_level"],
            "probability_pct": probability,
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
    previous = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
    payload = build(refresh_official=True, previous=previous)
    changed = policy_changed(previous, payload)
    if changed:
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")
    print(f"거시정책 신호 {payload['signal_id']} → {OUTPUT}")


def policy_changed(previous: dict, payload: dict) -> bool:
    # Verification lifecycle must reach consumers even when the decision stays
    # unchanged. Never rewrite the decision's original publication timestamp.
    if any(previous.get(key) != payload.get(key) for key in ("last_checked_at", "verification_status", "data_status", "next_check_at")):
        return True
    if previous.get("content_sha256") != payload.get("content_sha256"):
        return True
    if payload.get("quality_gate", {}).get("status") == "unavailable":
        return previous.get("quality_gate", {}).get("status") != "unavailable"
    return previous.get("expires_at") != payload.get("expires_at")


if __name__ == "__main__":
    main()
