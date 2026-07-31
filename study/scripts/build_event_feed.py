#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from event_feed import build_public_feed


ROOT = Path(__file__).resolve().parents[1]


def write_feed(output: Path, feed: dict) -> bool:
    if output.exists():
        try:
            current = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = None
        if current == feed:
            return False
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        json.dump(feed, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(output)
    return True


def _write_github_output(changed: bool) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"changed={'true' if changed else 'false'}\n")


def main() -> None:
    feed = build_public_feed()
    output = ROOT / "public" / "data" / "event-latest.json"
    changed = write_feed(output, feed)
    _write_github_output(changed)
    if changed:
        print(f"이벤트 feed {feed['feed_id']} → {output}")
    else:
        print(f"이벤트 feed 변경 없음: {feed['feed_id']}")


if __name__ == "__main__":
    main()
