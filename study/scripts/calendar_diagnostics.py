#!/usr/bin/env python3
"""Classify public calendar checks without response bodies or exception text."""
from __future__ import annotations
import argparse
import json
import os
import socket
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError


def safe_error(error: Exception, phase: str) -> dict:
    if isinstance(error, HTTPError):
        return {'error_type': 'http_error', 'http_status': error.code if isinstance(error.code, int) and 100 <= error.code <= 599 else None, 'phase': phase}
    if isinstance(error, URLError) and isinstance(error.reason, Exception):
        return safe_error(error.reason, phase)
    if isinstance(error, socket.gaierror):
        kind = 'dns_error'
    elif isinstance(error, ssl.SSLCertVerificationError):
        kind = 'tls_certificate_error'
    elif isinstance(error, (TimeoutError, socket.timeout)):
        kind = 'timeout'
    elif isinstance(error, ssl.SSLError):
        kind = 'tls_error'
    elif isinstance(error, (URLError, ConnectionError, OSError)):
        kind = 'connection_error'
    elif phase == 'parse':
        kind = 'parse_error'
    else:
        kind = 'unexpected_error'
    return {'error_type': kind, 'http_status': None, 'phase': phase}


def safe_checks(calendar: dict) -> dict:
    fields = ('ticker', 'monitor_id', 'status', 'checked_at', 'phase', 'error_type', 'http_status', 'event_count')
    return {scope: [{key: row.get(key) for key in fields if key in row} for row in calendar.get(scope + '_calendar_checks', [])] for scope in ('earnings', 'macro')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--calendar', type=Path, default=Path(__file__).resolve().parents[1] / 'data/event-calendar.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    calendar = json.loads(args.calendar.read_text())
    checks = safe_checks(calendar)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'calendar-checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2) + '\n')
    (args.output / 'public-event-calendar.json').write_text(json.dumps(calendar, ensure_ascii=False, indent=2) + '\n')
    lines = ['### Official calendar source checks', '', '| Source | Status | Phase | Error | HTTP |', '| --- | --- | --- | --- | --- |']
    for rows in checks.values():
        for row in rows:
            values = (row.get('ticker') or row.get('monitor_id'), row.get('status'), row.get('phase'), row.get('error_type'), row.get('http_status'))
            lines.append('| ' + ' | '.join(str(value) if value is not None else '—' for value in values) + ' |')
    summary = '\n'.join(lines) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as handle:
            handle.write(summary)


if __name__ == '__main__':
    main()
