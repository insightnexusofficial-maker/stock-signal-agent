#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import ssl
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sayo_quant import apply_peer_context, criteria_digest, decode_firestore_document, normalize_stock


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
                stocks.append(normalized)
    stocks = apply_peer_context(stocks)
    expires = now + timedelta(days=8)
    return {
        "schema_version": "1.2",
        "logic_version": criteria_config["logic_version"],
        "rating_logic_version": "study-rating-v2-2026-07-19",
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
        "stocks": stocks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock SAYO 공개 정량 스냅샷을 Study 계약으로 변환")
    parser.add_argument("--url", default=os.getenv("SAYO_PUBLIC_SNAPSHOT_URL", DEFAULT_URL))
    parser.add_argument("--output", type=Path, default=ROOT / "public/data/quant-latest.json")
    parser.add_argument("--input", type=Path, help="네트워크 대신 Firestore REST JSON 파일 사용")
    args = parser.parse_args()

    criteria = json.loads((ROOT / "data/sayo-criteria.json").read_text(encoding="utf-8"))
    document = json.loads(args.input.read_text(encoding="utf-8")) if args.input else fetch_document(args.url)
    snapshot = build_snapshot(document, criteria, datetime.now(KST))
    if not snapshot["stocks"]:
        raise RuntimeError("빈 Stock SAYO 스냅샷은 발행하지 않습니다")
    atomic_json_write(args.output, snapshot)
    print(f"정량 스냅샷 {len(snapshot['stocks'])}개 종목 → {args.output}")


if __name__ == "__main__":
    main()
