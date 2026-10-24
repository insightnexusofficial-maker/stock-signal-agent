import json
import socket
import ssl
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from calendar_diagnostics import safe_error, safe_checks
import discover_earnings_calendar
import discover_macro_calendar


class CalendarDiagnosticsTests(unittest.TestCase):
    def test_http_error_never_includes_response_or_headers(self):
        error = HTTPError('https://official.invalid', 403, 'private response marker', {'private': 'header-marker'}, None)
        result = safe_error(error, 'fetch')
        error.close()
        self.assertEqual(result, {'error_type':'http_error','http_status':403,'phase':'fetch'})
        self.assertNotIn('marker',json.dumps(result))

    def test_nested_transport_errors_and_parse_are_separate(self):
        self.assertEqual(safe_error(URLError(socket.gaierror('private dns message')),'fetch')['error_type'],'dns_error')
        self.assertEqual(safe_error(URLError(ssl.SSLCertVerificationError('private cert message')),'fetch')['error_type'],'tls_certificate_error')
        self.assertEqual(safe_error(URLError(TimeoutError('private timeout')),'fetch')['error_type'],'timeout')
        self.assertEqual(safe_error(ValueError('private body'),'parse')['error_type'],'parse_error')

    def test_safe_check_projection_drops_raw_diagnostic_fields(self):
        output=safe_checks({'earnings_calendar_checks':[{'ticker':'NVDA','status':'failed','error_type':'http_error','http_status':403,'response_body':'private','headers':{'private':'value'},'error':'private'}]})
        self.assertNotIn('private',json.dumps(output))
        self.assertEqual(output['earnings'][0]['http_status'],403)

    def test_earnings_parse_failure_is_recorded_without_renewal(self):
        calendar=json.loads((ROOT/'data/event-calendar.json').read_text())
        calendar['monitors']=[next(m for m in calendar['monitors'] if m['id']=='earnings-NVDA')]
        original_expiry=calendar['expires_at']
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'calendar.json';file.write_text(json.dumps(calendar))
            with mock.patch.object(discover_earnings_calendar,'CALENDAR_PATH',file), mock.patch.object(discover_earnings_calendar,'discover_official_earnings_date',side_effect=ValueError('private body')):
                discover_earnings_calendar.discover(datetime.fromisoformat('2026-10-24T22:45:00+09:00'),fetcher=lambda _: '<html>fixture</html>')
            result=json.loads(file.read_text())
        self.assertEqual(result['expires_at'],original_expiry)
        self.assertEqual(result['earnings_calendar_checks'][0]['phase'],'parse')
        self.assertEqual(result['earnings_calendar_checks'][0]['error_type'],'parse_error')
        self.assertNotIn('private body',json.dumps(result))

    def test_macro_sources_continue_after_first_fetch_failure(self):
        calendar=json.loads((ROOT/'data/event-calendar.json').read_text())
        calendar['monitors']=[m for m in calendar['monitors'] if m['id'] in {'macro-us-cpi','macro-us-jobs'}]
        calls=[]
        def fetch(url):
            calls.append(url)
            if 'cpi' in url:
                error=HTTPError(url,403,'private body',{},None);error.close();raise error
            return '<table><tr>November 6, 2026 08:30 AM October 2026</tr></table>'
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'calendar.json';file.write_text(json.dumps(calendar))
            with mock.patch.object(discover_macro_calendar,'CALENDAR_PATH',file):
                checks=discover_macro_calendar.discover(datetime.fromisoformat('2026-10-24T22:45:00+09:00'),fetcher=fetch)
            result=json.loads(file.read_text())
        self.assertEqual(len(calls),2)
        self.assertEqual(checks[0]['http_status'],403)
        self.assertEqual(checks[1]['status'],'confirmed')
        self.assertEqual(result['expires_at'],calendar['expires_at'])
