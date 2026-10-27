"""Keyless, bounded Federal Reserve policy observation shared by both consumers.

This reports an actual decision, never a forecast. A failed recheck preserves a
still-valid last verified decision with an explicit failed verification state.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import socket
import ssl
from datetime import datetime, time, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

KST = timezone(timedelta(hours=9))
EASTERN = ZoneInfo("America/New_York")
CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
HOST = "www.federalreserve.gov"
MAX_BYTES = 2_000_000
STATEMENT_PATH = re.compile(r"^/newsevents/pressreleases/monetary(\d{8})a\.htm$")
MONTHS = {name: index for index, name in enumerate(("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")) if name}
MONTH_PATTERN = "(?:" + "|".join(MONTHS) + ")"
NUMBER = r"\d+(?:-\d+/\d+|/\d+|\.\d+)?"
TARGET = re.compile(rf"target range for the federal funds rate\s+(?:at|(?:by\s+{NUMBER}\s+percentage\s+points?\s+)?to)\s+({NUMBER})\s+to\s+({NUMBER})\s+percent", re.I)


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.parts, self.ignored = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if tag == "a" and dict(attrs).get("href"):
            self.links.append(dict(attrs)["href"])

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, value):
        if not self.ignored and value.strip():
            self.parts.append(" ".join(value.split()))


def _page(body):
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BYTES:
        raise ValueError("invalid official policy document")
    page = Page()
    page.feed(body)
    return page, " ".join(page.parts)


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _iso(value):
    return value.astimezone(KST).isoformat(timespec="seconds")


def fetch_official(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.username or parsed.password:
        raise ValueError("official Federal Reserve URL required")
    request = Request(url, headers={"User-Agent": "stock-sayo-official-policy/1.0"})
    with urlopen(request, timeout=12) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname != HOST:
            raise ValueError("official policy redirect rejected")
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("official policy download too large")
        return payload.decode("utf-8")


def latest_statement(calendar_html, now):
    page, text = _page(calendar_html)
    candidates = []
    for link in page.links:
        url = urljoin(CALENDAR_URL, link)
        parsed = urlparse(url)
        match = STATEMENT_PATH.fullmatch(parsed.path)
        if parsed.scheme != "https" or parsed.hostname != HOST or not match:
            continue
        day = datetime.strptime(match[1], "%Y%m%d").date()
        if day <= now.astimezone(EASTERN).date():
            candidates.append((day, url))
    if not candidates:
        raise ValueError("no published official statement")
    day, url = max(candidates)
    meetings = []
    for year in (now.year, now.year + 1):
        section = re.search(rf"{year}\s+FOMC Meetings(.*?)(?=20\d{{2}}\s+FOMC Meetings|$)", text)
        if not section:
            continue
        # Minutes are released weeks after the decision. Their dates appear in
        # the same year section and must never supersede a policy decision.
        meeting_text = re.sub(rf"\(?Released\s+{MONTH_PATTERN}\s+\d{{1,2}},?\s+20\d{{2}}\)?", "", section[1], flags=re.I)
        for match in re.finditer(rf"({MONTH_PATTERN})\s+(\d{{1,2}})(?:\s*[-–]\s*(\d{{1,2}}))?\*?", meeting_text):
            meeting = datetime(year, MONTHS[match[1]], int(match[3] or match[2])).date()
            if meeting > day:
                meetings.append(meeting)
    return day, url, min(meetings) if meetings else None


def _number(value):
    if "-" in value:
        whole, fraction = value.split("-", 1)
        numerator, denominator = fraction.split("/")
        return float(whole) + float(numerator) / float(denominator)
    if "/" in value:
        numerator, denominator = value.split("/")
        return float(numerator) / float(denominator)
    return float(value)


def parse_statement(body, expected_date, now):
    _, text = _page(body)
    published = re.search(rf"({MONTH_PATTERN})\s+(\d{{1,2}}),\s+(20\d{{2}})", text)
    clock = re.search(r"For release at\s+(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.)\s+(EDT|EST)", text, re.I)
    ranges = TARGET.findall(text)
    if "Federal Reserve issues FOMC statement" not in text or not published or not clock or len(ranges) != 1:
        raise ValueError("official policy statement structure unverified")
    day = datetime(int(published[3]), MONTHS[published[1]], int(published[2])).date()
    if day != expected_date:
        raise ValueError("official policy publication date mismatch")
    hour = int(clock[1]) % 12 + (12 if clock[3].lower() == "p.m." else 0)
    released = datetime.combine(day, time(hour, int(clock[2])), EASTERN)
    if released.tzname() != clock[4].upper() or released > now:
        raise ValueError("official policy release time unverified")
    lower, upper = map(_number, ranges[0])
    if not all(math.isfinite(x) for x in (lower, upper)) or not 0 <= lower <= upper <= 25:
        raise ValueError("official policy range invalid")
    action = re.search(r"(?:Committee|FOMC)\s+(?:decided|voted)\s+to\s+(raise|lower|maintain)", text, re.I)
    direction = {"raise": "hike", "lower": "cut", "maintain": "hold"}.get(action[1].lower(), "mixed") if action else "mixed"
    return released, lower, upper, direction


def _contract(core):
    content = {key: core.get(key) for key in ("signal_id", "sector", "evidence")}
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**core, "schema_version": "1.0", "logic_version": "official-policy-v1-2026-10-28", "content_sha256": digest}


def _previous_contract(previous):
    if not isinstance(previous, dict):
        return {}
    previous = copy.deepcopy(previous or {})
    if "sector" in previous:
        return previous
    if previous.get("mode") != "official-policy-decision":
        return {}
    sector = {key: previous.get(key) for key in ("status", "direction", "probability_level", "probability_pct", "confidence", "display_label", "chip_tone", "horizon", "summary", "target_range_lower", "target_range_upper", "source_published_at")}
    sector.update({"id": "us_rate_policy", "label": "미국 금리정책", "primary_source_url": previous.get("source_url"), "primary_source_name": "Federal Reserve", "review_status": "verified", "evidence_ids": [previous.get("signal_id")]})
    return _contract({**{key: value for key, value in previous.items() if key not in sector}, "sector": sector, "quality_gate": {"status": previous.get("quality_status"), "mode": previous.get("mode"), "evidence_count": 1}, "evidence": [{"id": previous.get("signal_id"), "source_url": previous.get("source_url"), "published_at": previous.get("source_published_at")} ]})


def _valid_previous(previous, now):
    sector = previous.get("sector") or {}
    published, expiry = _stamp(sector.get("source_published_at")), _stamp(previous.get("expires_at"))
    try:
        lower, upper = float(sector["target_range_lower"]), float(sector["target_range_upper"])
    except (KeyError, TypeError, ValueError):
        return False
    url = urlparse(str(sector.get("primary_source_url") or ""))
    return (previous.get("quality_gate", {}).get("mode") == "official-policy-decision"
            and previous.get("quality_gate", {}).get("status") == "passed"
            and bool(published and expiry and published <= now < expiry <= published + timedelta(days=62))
            and url.scheme == "https" and url.hostname == HOST and bool(STATEMENT_PATH.fullmatch(url.path))
            and all(math.isfinite(x) for x in (lower, upper)) and 0 <= lower <= upper <= 25)


def refresh_official_policy(previous=None, now=None, fetcher=None):
    """At most two public requests; successful cache 6h, failed recheck 1h."""
    now = now or datetime.now(KST)
    old = _previous_contract(previous)
    valid = _valid_previous(old, now)
    checked, next_check = _stamp(old.get("last_checked_at")), _stamp(old.get("next_check_at"))
    meeting_day = old.get("next_decision_date") == now.astimezone(EASTERN).date().isoformat()
    if checked and next_check and checked <= now < next_check and not (meeting_day and now - checked >= timedelta(hours=1)) and (valid or old.get("quality_gate", {}).get("status") == "unavailable"):
        return old
    fetcher = fetcher or fetch_official
    phase = "fetch"
    try:
        calendar = fetcher(CALENDAR_URL)
        phase = "parse"
        day, url, next_day = latest_statement(calendar, now)
        phase = "fetch"
        statement = fetcher(url)
        phase = "parse"
        published, lower, upper, direction = parse_statement(statement, day, now)
        expiry = published + timedelta(days=62)
        if next_day:
            expiry = min(expiry, datetime.combine(next_day + timedelta(days=1), time.min, EASTERN))
        if expiry <= now:
            raise ValueError("official decision supersession window expired")
        evidence_id = f"fomc-decision-{day.isoformat()}"
        next_check = now + timedelta(hours=1 if next_day and next_day <= now.astimezone(EASTERN).date() else 6)
        return _contract({
            "signal_id": "official-decision-" + day.isoformat(), "generated_at": _iso(now), "expires_at": _iso(expiry),
            "quality_gate": {"status": "passed", "mode": "official-policy-decision", "evidence_count": 1, "message": "공식 결정 사실이며 미래 정책 확률을 뜻하지 않습니다."},
            "data_status": "current", "verification_status": "success", "last_checked_at": _iso(now), "last_success_at": _iso(now), "next_check_at": _iso(min(next_check, expiry)),
            "next_decision_date": next_day.isoformat() if next_day else None, "observation_precision": "time", "calendar_source_url": CALENDAR_URL,
            "sector": {"id": "us_rate_policy", "label": "미국 금리정책", "status": "neutral", "direction": direction, "probability_level": "low", "probability_pct": None, "confidence": 85, "display_label": f"공식 정책금리 {lower:g}–{upper:g}%", "chip_tone": "neutral", "horizon": "최근 공식 결정", "summary": f"공식 목표금리 범위는 {lower:g}–{upper:g}%입니다. 다음 결정 전까지 유지되는 최근 결정이며 미래 인상·인하 확률은 표시하지 않습니다.", "primary_source_name": "Federal Reserve", "primary_source_url": url, "source_published_at": _iso(published), "review_status": "verified", "evidence_ids": [evidence_id], "target_range_lower": lower, "target_range_upper": upper},
            "evidence": [{"id": evidence_id, "source_name": "Federal Reserve", "source_family": "federal_reserve_policy_decision", "source_url": url, "published_at": _iso(published), "fact": f"공식 목표금리 범위 {lower:g}–{upper:g}%"}],
        })
    except Exception as error:
        # No raw exception text, response body, headers or credentials are stored.
        reason = error.reason if isinstance(error, URLError) else error
        kind = ("timeout" if isinstance(reason, TimeoutError) else "tls_certificate_error" if isinstance(reason, ssl.SSLCertVerificationError) else "dns_error" if isinstance(reason, socket.gaierror) else "http_error" if isinstance(error, HTTPError) else "parse_error" if phase == "parse" else "fetch_error")
        result = old if valid else _contract({"signal_id": None, "generated_at": _iso(now), "expires_at": _iso(now), "quality_gate": {"status": "unavailable", "mode": "official-policy-decision", "evidence_count": 0}, "sector": {}, "evidence": []})
        result.update({"data_status": "last_verified" if valid else "unavailable", "verification_status": "failed", "last_checked_at": _iso(now), "next_check_at": _iso(now + timedelta(hours=1)), "verification_error": kind})
        return result
