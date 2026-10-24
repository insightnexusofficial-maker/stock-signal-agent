#!/usr/bin/env python3
"""Check publication readiness and the public artifact identity, without secrets.

Job completion, evidence review, data age and public publication are independent
checks. This command never renews timestamps or changes an artifact.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def validate_artifacts(documents: dict, now: datetime, event_only: bool = False, selected_names: list[str] | None = None) -> list[str]:
    errors = []
    names = selected_names or (["event-latest.json"] if event_only else ["cycle-latest.json", "quant-latest.json"])
    for name in names:
        document = documents.get(name) or {}
        expiry = timestamp(document.get("expires_at"))
        generated = timestamp(document.get("generated_at"))
        unavailable_policy = name == "macro-policy-latest.json" and document.get("quality_gate", {}).get("status") == "unavailable"
        if not unavailable_policy and (not expiry or expiry <= now):
            errors.append(f"{name}: expired-or-unverified-expiry")
        if not generated or generated > now + timedelta(minutes=5):
            errors.append(f"{name}: invalid-generated-at")
        if name != "quant-latest.json" and not unavailable_policy and document.get("quality_gate", {}).get("status") != "passed":
            errors.append(f"{name}: evidence-or-calendar-review-incomplete")
        if name == "event-latest.json":
            checks = document.get("event_sync", {}).get("calendar_checks", {})
            if any(row.get("status") == "failed" for rows in checks.values() for row in rows):
                errors.append(f"{name}: calendar-source-check-failed")
        if name == "cycle-latest.json" and not document.get("evidence"):
            errors.append(f"{name}: no-verified-evidence")
        if name == "quant-latest.json":
            source_at = timestamp(document.get("source_collection_finished_at"))
            if not source_at or now - source_at > timedelta(days=4) or source_at > now + timedelta(minutes=5):
                errors.append(f"{name}: market-source-old-or-unverified")
            if document.get("source_collection_status") not in {"success", "complete"}:
                errors.append(f"{name}: market-source-not-complete")
    return errors


def identity_errors(local: dict, public: dict, name: str) -> list[str]:
    fields = {
        "cycle-latest.json": ("report_id", "evidence_sha256", "generated_at", "expires_at"),
        "quant-latest.json": ("generated_at", "expires_at", "criteria_sha256", "source_collection_finished_at"),
        "event-latest.json": ("feed_id", "content_sha256", "generated_at", "expires_at"),
        "macro-policy-latest.json": ("content_sha256", "generated_at", "expires_at"),
    }[name]
    return [f"{name}: public-mismatch-{field}" for field in fields if not local.get(field) or public.get(field) != local.get(field)]


def fetch_public(url: str) -> dict:
    if not url.startswith("https://stock-sayo-study.web.app/data/"):
        raise ValueError("Only the configured public Study artifacts may be checked")
    request = Request(url, headers={"User-Agent": "stock-sayo-publication-health/1.0", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--event-only", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--deployment-ready", choices=("true", "false"))
    parser.add_argument("--verify-public", action="store_true")
    parser.add_argument("--artifacts", nargs="+", choices=("cycle-latest.json", "quant-latest.json", "event-latest.json", "macro-policy-latest.json"))
    args = parser.parse_args()
    if args.preflight:
        if args.deployment_ready != "true":
            raise SystemExit("Publication readiness: deployment-unavailable; no complete publication can be claimed")
        print("Publication readiness: deployment route available")
        return
    names = args.artifacts or (["event-latest.json"] if args.event_only else ["cycle-latest.json", "quant-latest.json"])
    documents = {name: json.loads((args.root / "public/data" / name).read_text()) for name in names}
    errors = validate_artifacts(documents, datetime.now(KST), args.event_only, names)
    if args.verify_public and not errors:
        for name, local in documents.items():
            try:
                public = fetch_public("https://stock-sayo-study.web.app/data/" + name)
                errors.extend(identity_errors(local, public, name))
            except Exception as error:
                errors.append(f"{name}: public-verification-{type(error).__name__}")
    if errors:
        for error in errors:
            print("Publication incomplete:", error)
        raise SystemExit(1)
    print("Publication health:", "public artifact identity verified" if args.verify_public else "local artifacts ready")


if __name__ == "__main__":
    main()
