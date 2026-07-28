import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "public" / "index.html"


class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, _tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])


class FrontendEntryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INDEX.read_text(encoding="utf-8")

    def test_connection_error_has_explicit_retry_ui(self):
        parser = _IdCollector()
        parser.feed(self.source)
        self.assertIn("gate-connection", parser.ids)
        self.assertIn("gate-retry", parser.ids)
        self.assertIn('state: "connection_error"', self.source)
        self.assertIn("showConnectionError();", self.source)

    def test_approval_is_server_checked_with_bounded_retry(self):
        self.assertIn("getDocFromServer(doc(db, \"fcm_tokens\", token))", self.source)
        self.assertRegex(self.source, r"const APPROVAL_TIMEOUT_MS = \d+;")
        self.assertRegex(self.source, r"const APPROVAL_MAX_ATTEMPTS = 2;")
        self.assertIn("withTimeout(", self.source)
        self.assertIn("snap.metadata.fromCache", self.source)
        self.assertNotIn("statusKnown", self.source)

    def test_messaging_is_lazy_and_not_a_static_module_dependency(self):
        static_imports = re.findall(r'^\s*import\s+.*?from\s+"([^"]+)";', self.source, re.MULTILINE)
        self.assertFalse(any("firebase-messaging" in url for url in static_imports))
        self.assertIn("import(MESSAGING_SDK_URL)", self.source)
        self.assertIn("void runGateCheck();", self.source)
        self.assertNotIn("await initServiceWorker();", self.source)
        self.assertNotIn("await rotateLegacyToken();", self.source)
        self.assertIn("if (legacyCleanupPromise) return legacyCleanupPromise;", self.source)
        self.assertRegex(
            self.source,
            r"\.catch\(\(\) => \{\s*legacyCleanupScheduled = false;[\s\S]*?return false;",
        )
        cleanup_await = self.source.index("legacyCleanupNeeded && !(await scheduleLegacyTokenCleanup())")
        new_token_request = self.source.index("infrastructure.sdk.getToken", cleanup_await)
        self.assertLess(cleanup_await, new_token_request)

    def test_last_good_is_a_single_conditional_read_not_a_subscription(self):
        self.assertEqual(self.source.count("onSnapshot(dataDoc"), 1)
        self.assertEqual(self.source.count("onSnapshot(lastGoodDoc"), 0)
        self.assertEqual(self.source.count("getDoc(lastGoodDoc)"), 1)
        self.assertIn("marketDataNeedsFallback(liveMarketData)", self.source)
        self.assertIn("collectionStatusNeedsFallback(data.collection_status)", self.source)
        self.assertIn('["partial", "failed", "running"]', self.source)
        self.assertIn("fallbackLoadPromise", self.source)

    def test_app_version_changes_for_the_entry_flow_release(self):
        self.assertIn('const APP_VERSION = "20260721-entry-flow-1";', self.source)

    def test_peg_source_is_a_detail_caption_not_a_metric_widget(self):
        self.assertIn('`<div class="detail-caption">PEG 출처: ${pegSourceName}</div>`', self.source)
        self.assertIn("${pegSourceCaptionHtml}", self.source)
        self.assertNotIn('{ key: "PEG 기준"', self.source)

    def test_official_event_feed_is_display_only(self):
        parser = _IdCollector()
        parser.feed(self.source)
        self.assertIn("event-calendar-list", parser.ids)
        self.assertIn("renderEventCalendar(data);", self.source)
        self.assertIn("nextEventForTicker(stock.code)", self.source)
        self.assertIn("다음 공식 실적:", self.source)
        signal_function = self.source[
            self.source.index("function check"):self.source.index("function renderMacro")
        ] if "function check" in self.source else ""
        self.assertNotIn("event_calendar", signal_function)

    def test_sensitive_values_are_not_used_in_performance_entries(self):
        self.assertIn('perfMark("market-subscribe-start")', self.source)
        self.assertIn('perfMark("gate-hidden")', self.source)
        self.assertIn('perfMeasure("notification-init"', self.source)
        performance_calls = "\n".join(
            line for line in self.source.splitlines() if "perfMark(" in line or "perfMeasure(" in line
        )
        self.assertNotIn("perfMark(token", performance_calls)
        self.assertNotIn("perfMark(stored", performance_calls)
        self.assertNotIn("perfMeasure(token", performance_calls)
        self.assertNotIn("nickname", performance_calls.lower())


if __name__ == "__main__":
    unittest.main()
