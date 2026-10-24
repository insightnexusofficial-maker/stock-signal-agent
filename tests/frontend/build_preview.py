"""Build a local-only synthetic preview with real UI code and zero Firebase I/O.

Usage: python3 -B tests/frontend/build_preview.py /private/tmp/sayo-ui-preview
Serve that output folder on localhost. No credentials or browser state are read.
"""
from datetime import date, timedelta
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def public_market_snapshot(path: Path) -> dict:
    """Decode only a saved public stocks/data REST document, never account data."""
    document = json.loads(path.read_text())
    expected = "projects/stock-sayo/databases/(default)/documents/stocks/data"
    if document.get("name") != expected:
        raise ValueError("Preview input must be the public stocks/data REST document")
    def value(field):
        if "mapValue" in field:
            return {key: value(item) for key, item in field["mapValue"].get("fields", {}).items()}
        if "arrayValue" in field:
            return [value(item) for item in field["arrayValue"].get("values", [])]
        for key in ("stringValue", "timestampValue", "booleanValue", "doubleValue"):
            if key in field:
                return field[key]
        if "integerValue" in field:
            return int(field["integerValue"])
        return None
    return {key: value(item) for key, item in document.get("fields", {}).items()}


def build(output: Path, snapshot: Path | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    for name in ("sayo-ui.js", "sayo-ui.css", "sayo-logo-20260712-transparent.png"):
        shutil.copy2(ROOT / "public" / "assets" / name, assets / name)
    history = [
        {"date": (date(2026, 10, 1) + timedelta(days=i)).isoformat(), "value": 18 + i * .12}
        for i in range(20)
    ]
    def metric(value, **extra):
        return {"current": value, "observed_at": "2026-10-23", "data_status": "fresh", "source_url": "https://example.com/official", "history": history, **extra}
    stock = {
        "code": "SAMPLE", "name": "디자인 검증용 기업", "sector": "ai_bigtech",
        "price": 123.45, "rsi": 42.5, "peg_fwd": .72, "per_fwd": 18.5,
        "step1": True, "step2": True, "buy_level": "candidate",
        "cycle_status": "neutral", "cycle_assessment_status": "insufficient",
        "cycle_data_status": "current",
        "cycle_evidence": [{"id": "synthetic-fact", "fact": "합성 예시: 공식 비교 수치가 유지됩니다.", "source_url": "https://example.com/official", "published_at": "2026-10-23", "direction": "positive"}],
        "cycle_changes": [{"from": "favorable", "to": "neutral", "assessment_to": "insufficient"}], "cycle_reason": "합성 예시: 서로 독립된 근거가 부족합니다.",
        "cycle_hypotheses": ["다음 공식 발표에서 투자계획과 현금흐름을 확인합니다."],
        "cycle_last_reviewed_at": "2026-10-24T08:00:00+09:00",
        "cycle_next_check_at": "2026-10-31T08:00:00+09:00",
    }
    data = {
        "updated": "합성 데이터 · 10월 24일 08:00", "collection_status": "success",
        "vix": metric(19.45, ma5=18.3, mode="normal", is_stale=True),
        "qqq": metric(510, price=510, ma20=505, above_ma20=True, deviation_pct=.99),
        "kospi": metric(2700, price=2700, ma20=2750, above_ma20=False, deviation_pct=-1.82),
        "usdkrw": metric(1350, prev=1347, change_pct=.22),
        "market_mode_us": "unknown", "market_mode_kr": "unknown",
        "us_stock": [stock], "kr_stock": [{**stock, "code": "000000", "name": "검증용 한국 기업", "price": 123400}],
        "kr_etf": [{**stock, "code": "0000X0", "name": "매우 긴 이름의 한국 상장 글로벌 산업 ETF 검증용", "price": 18230, "nav_discount": -.3}],
        "market_environment": {"quality_status": "partial", "summary": "합성 예시: 금리·신용 자료의 관측 기준을 확인합니다.", "display_only": True,
            "axes": [{"id": "rates", "label": "금리 부담", "status": "available", "summary": "예측 없이 공식 관측값을 확인합니다."}],
            "series": {"us_2y": metric(4.2, unit="%"), "us_real_10y": metric(2.1, unit="%"), "usd_broad": metric(120, unit="index"), "nfci": metric(-.3, unit="index")}},
        "event_sync_status": "unavailable",
        "cycle_report": {"data_status": "fresh", "quality_status": "insufficient", "generated_at": "2026-10-24T08:00:00+09:00", "expires_at": "2026-10-31T08:00:00+09:00"},
    }
    if snapshot:
        data = public_market_snapshot(snapshot)
    source = (ROOT / "public" / "index.html").read_text()
    match = re.search(r'<script type="module">(.*?)</script>', source, re.S)
    module = match.group(1)
    module = re.sub(r'^\s*import .*?https://www\.gstatic\.com.*?;\s*$', "", module, flags=re.M)
    module = re.sub(r'    const firebaseConfig = \{.*?    const db = getFirestore\(app\);', "    const db = {};", module, flags=re.S)
    module = module[:module.index("    const VAPID_KEY")]
    module = module.replace("    clearLegacyPwaCaches();", "")
    mocks = """
      const previewData = PREVIEW_DATA;
      const doc = (_db, collection, id) => ({collection, id});
      const getDoc = async () => ({exists:()=>false});
      const onSnapshot = (_ref, callback) => {queueMicrotask(()=>callback({exists:()=>true,data:()=>previewData}));return ()=>{};};
    """.replace("PREVIEW_DATA", json.dumps(data, ensure_ascii=False))
    module = mocks + module + """
      let previewPush = true;
      sayoUi = createSayoUI({getPushState:()=>({registered:true,supported:true,enabled:previewPush,permission:'granted'}),setPushEnabled:async enabled=>{previewPush=enabled;}});
      document.querySelector('#gate-screen').classList.add('hidden');
    """
    source = source[:match.start()] + '<script type="module">' + module + '</script>' + source[match.end():]
    notice = "공개 시장자료 미리보기 · 알림 등록과 사용자 정보에 연결되지 않습니다." if snapshot else "합성 데이터 미리보기 · 운영 서비스와 연결되지 않습니다."
    source = source.replace('<div class="app">', f'<div class="app"><p style="padding:8px 0;color:var(--fg-2);font-size:12px;line-height:1.5">{notice}</p>', 1)
    source = source.replace('<link rel="manifest" href="/manifest.json">', '')
    (output / "index.html").write_text(source)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--public-snapshot", type=Path)
    args = parser.parse_args()
    build(args.output, args.public_snapshot)
