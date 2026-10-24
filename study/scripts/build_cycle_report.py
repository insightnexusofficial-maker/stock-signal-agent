#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cycle_engine import build_report


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    evidence_document = json.loads((ROOT / "data/evidence.json").read_text(encoding="utf-8"))
    company_map = json.loads((ROOT / "data/company-cycle-map.json").read_text(encoding="utf-8"))
    output = ROOT / "public/data/cycle-latest.json"
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    report = build_report(
        evidence_document.get("evidence", []),
        companies=company_map.get("companies", []),
        company_map_version=company_map.get("version"),
        previous_report=previous,
    )
    inbox_path = ROOT / "data/inbox.json"
    inbox = json.loads(inbox_path.read_text()) if inbox_path.exists() else {}
    report["collection_coverage"] = {"last_attempt_at": inbox.get("generated_at"), "sources": [{key: row.get(key) for key in ("source_id", "status", "items", "dated_items", "evidence_status")} for row in inbox.get("source_results", [])]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(output)
    print(f"사이클 리포트 {report['report_id']} → {output}")


if __name__ == "__main__":
    main()
