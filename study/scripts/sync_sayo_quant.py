#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import ssl
import tempfile
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sayo_quant import (
    apply_peer_context,
    apply_post_earnings_adjustment,
    criteria_digest,
    decode_firestore_document,
    normalize_stock,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://firestore.googleapis.com/v1/projects/stock-sayo/databases/(default)/documents/stocks/data"
KST = timezone(timedelta(hours=9))


def atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def fetch_document(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "stock-sayo-study/1.0"})
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=25, context=context) as response:
        if response.status != 200:
            raise RuntimeError(f"Stock SAYO snapshot HTTP {response.status}")
        return json.load(response)


def build_snapshot(document: dict, criteria_config: dict, now: datetime) -> dict:
    decoded = decode_firestore_document(document)
    stocks = []
    for market, key in (("kr", "kr_stock"), ("us", "us_stock")):
        for stock in decoded.get(key, []) or []:
            normalized = normalize_stock(stock, market, criteria_config["criteria"])
            if normalized["ticker"]:
                normalized["assessment"] = {
                    "state": "initial",
                    "label": "첫 기업 판단",
                    "updated_at": now.isoformat(timespec="seconds"),
                }
                stocks.append(normalized)
    stocks = apply_peer_context(stocks)
    expires = now + timedelta(days=8)
    return {
        "schema_version": "1.2",
        "logic_version": criteria_config["logic_version"],
        "rating_logic_version": "study-company-assessment-v3-2026-07-31",
        "criteria_sha256": criteria_digest(criteria_config),
        "generated_at": now.isoformat(timespec="seconds"),
        "expires_at": expires.isoformat(timespec="seconds"),
        "source": "Stock SAYO public Firestore snapshot",
        "source_updated_at": decoded.get("updated"),
        "alignment": {
            "mode": "reproduce-and-verify-step1",
            "aligned": sum(1 for stock in stocks if stock["sayo_alignment"] == "aligned"),
            "drift": sum(1 for stock in stocks if stock["sayo_alignment"] == "drift"),
            "pending": sum(1 for stock in stocks if stock["sayo_alignment"] == "pending"),
        },
        "assessment_policy": {
            "mode": "hold-until-verified-earnings",
            "eps_revision_lowered_threshold": -1.0,
            "forward_eps_growth_drop_threshold_pp": -5.0,
        },
        "stocks": stocks,
    }


def merge_earnings_lifecycle(
    previous: dict,
    refreshed: dict,
    calendar: dict,
    results: dict,
    now: datetime,
) -> dict:
    """발표 전에는 직전 판단을 유지하고, 발표 후 새 전망만 반영한다."""

    event_by_id = {item["id"]: item for item in calendar.get("events") or []}
    latest_result_by_ticker = {}
    for result in results.get("results") or []:
        event = event_by_id.get(result.get("event_id"))
        if (
            not event
            or event.get("kind") != "earnings"
            or result.get("status") != "complete"
            or result.get("review_status") != "verified"
        ):
            continue
        ticker = str(event.get("ticker") or "").upper()
        retrieved = datetime.fromisoformat(result["retrieved_at"])
        current = latest_result_by_ticker.get(ticker)
        if not current or retrieved > current[0]:
            latest_result_by_ticker[ticker] = (retrieved, result)

    refreshed_by_ticker = {item["ticker"]: item for item in refreshed.get("stocks") or []}
    merged = []
    for old in previous.get("stocks") or []:
        ticker = old["ticker"]
        fresh = refreshed_by_ticker.get(ticker)
        latest = latest_result_by_ticker.get(ticker)
        assessment_updated = old.get("assessment", {}).get("updated_at")
        cutoff = datetime.fromisoformat(
            assessment_updated or previous["generated_at"]
        )
        if not latest or latest[0] <= cutoff or not fresh:
            held = deepcopy(old)
            held["assessment"] = {
                **(held.get("assessment") or {}),
                "state": "held_until_next_earnings",
                "label": "다음 실적 발표 전 기존 판단 유지",
            }
            merged.append(held)
            continue
        if str(fresh.get("data_as_of") or "") <= str(old.get("data_as_of") or ""):
            held = deepcopy(old)
            held["assessment"] = {
                **(held.get("assessment") or {}),
                "state": "waiting_for_post_earnings_data",
                "label": "실적 발표 확인 · 새 EPS 전망 반영 대기",
                "event_id": latest[1]["event_id"],
            }
            merged.append(held)
            continue
        merged.append(apply_post_earnings_adjustment(old, fresh, latest[1], now))

    previous_tickers = {item["ticker"] for item in previous.get("stocks") or []}
    for fresh in refreshed.get("stocks") or []:
        if fresh["ticker"] not in previous_tickers:
            fresh["assessment"] = {
                "state": "initial",
                "label": "첫 기업 판단",
                "updated_at": now.isoformat(timespec="seconds"),
            }
            merged.append(fresh)
    output = deepcopy(refreshed)
    output["stocks"] = merged
    output["assessment_policy"] = {
        "mode": "hold-until-verified-earnings",
        "eps_revision_lowered_threshold": -1.0,
        "forward_eps_growth_drop_threshold_pp": -5.0,
    }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock SAYO 공개 정량 스냅샷을 Study 계약으로 변환")
    parser.add_argument("--url", default=os.getenv("SAYO_PUBLIC_SNAPSHOT_URL", DEFAULT_URL))
    parser.add_argument("--output", type=Path, default=ROOT / "public/data/quant-latest.json")
    parser.add_argument("--input", type=Path, help="네트워크 대신 Firestore REST JSON 파일 사용")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="기존 판단 유지 규칙을 사용하지 않고 전체를 초기화합니다.",
    )
    parser.add_argument("--calendar", type=Path, default=ROOT / "data/event-calendar.json")
    parser.add_argument("--event-results", type=Path, default=ROOT / "data/event-results.json")
    args = parser.parse_args()

    criteria = json.loads((ROOT / "data/sayo-criteria.json").read_text(encoding="utf-8"))
    document = json.loads(args.input.read_text(encoding="utf-8")) if args.input else fetch_document(args.url)
    now = datetime.now(KST)
    snapshot = build_snapshot(document, criteria, now)
    if args.output.exists() and not args.full_refresh:
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        calendar = json.loads(args.calendar.read_text(encoding="utf-8"))
        results = json.loads(args.event_results.read_text(encoding="utf-8"))
        snapshot = merge_earnings_lifecycle(previous, snapshot, calendar, results, now)
    if not snapshot["stocks"]:
        raise RuntimeError("빈 Stock SAYO 스냅샷은 발행하지 않습니다")
    atomic_json_write(args.output, snapshot)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write("changed=true\n")
    print(f"기업 판단 {len(snapshot['stocks'])}개 종목 → {args.output}")


if __name__ == "__main__":
    main()
