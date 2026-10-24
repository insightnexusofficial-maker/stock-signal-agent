import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from datetime import datetime, timezone

from collect_sources import parse_dated_html_watch, parse_rss  # noqa: E402


class SourceCollectorTests(unittest.TestCase):
    def test_atom_feed_keeps_direct_date_and_excerpt(self):
        body = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>Quarterly result</title><updated>2026-07-17T08:00:00Z</updated>
          <link href="https://example.com/result"/><summary>Revenue grew 12 percent.</summary></entry></feed>'''
        source = {
            "id": "official", "source_family": "company_ir",
            "segments": ["foundry_logic"], "default_pillar": "earnings",
        }
        candidates = parse_rss(source, body, "https://example.com/feed", 10)
        self.assertEqual(candidates[0]["published_at"], "2026-07-17")
        self.assertEqual(candidates[0]["published_at_quality"], "direct")
        self.assertIn("Revenue grew", candidates[0]["content_excerpt"])

    def test_release_index_does_not_assign_its_latest_date_to_page_facts(self):
        source = {"id":"index", "label":"Official index", "source_family":"company_ir", "segments":["foundry_logic"], "default_pillar":"earnings", "page_role":"release_index"}
        items = parse_dated_html_watch(source, b"<article>October 23, 2026 Conference</article><article>Revenue result July 1, 2026</article>", "https://example.com/index", datetime(2026, 10, 24, tzinfo=timezone.utc))
        self.assertIsNone(items[0]["published_at"])
        self.assertEqual(items[0]["published_at_quality"], "unknown")

    def test_dated_html_limits_excerpt_around_latest_direct_date(self):
        body = b'''<html><title>Official releases</title><body>
          <article>June 1, 2026 older item</article>
          <article>July 17, 2026 Latest equipment revenue increased 14 percent.</article>
        </body></html>'''
        source = {
            "id": "industry", "label": "Industry body", "source_family": "association",
            "segments": ["equipment_materials"], "default_pillar": "earnings",
        }
        candidates = parse_dated_html_watch(source, body, "https://example.com/releases", datetime(2026, 7, 18, tzinfo=timezone.utc))
        self.assertEqual(candidates[0]["published_at"], "2026-07-17")
        self.assertEqual(candidates[0]["published_at_quality"], "direct")
        self.assertIn("Latest equipment", candidates[0]["content_excerpt"])


if __name__ == "__main__":
    unittest.main()
