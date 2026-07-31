from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_event_feed


class BuildEventFeedTests(unittest.TestCase):
    def test_unchanged_feed_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "event-latest.json"
            feed = {"feed_id": "same", "events": []}
            output.write_text(
                json.dumps(feed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = output.stat().st_mtime_ns

            changed = build_event_feed.write_feed(output, feed)

            self.assertFalse(changed)
            self.assertEqual(output.stat().st_mtime_ns, before)

    def test_changed_feed_replaces_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "event-latest.json"
            output.write_text('{"feed_id":"old"}\n', encoding="utf-8")
            feed = {"feed_id": "new", "events": [{"id": "event"}]}

            changed = build_event_feed.write_feed(output, feed)

            self.assertTrue(changed)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                feed,
            )


if __name__ == "__main__":
    unittest.main()
