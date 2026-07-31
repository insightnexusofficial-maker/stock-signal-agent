#!/usr/bin/env python3
from __future__ import annotations

import email.utils
import hashlib
import json
import re
import ssl
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
MAX_BYTES = 2_000_000


def tls_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.in_title = False
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.ignored_depth += 1
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.ignored_depth:
            self.ignored_depth -= 1
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        self.text_parts.append(clean)
        if self.in_title:
            self.title_parts.append(clean)


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def candidate_id(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}\n{title}".encode()).hexdigest()[:24]


def fetch(source: dict, now: datetime) -> tuple[bytes, str]:
    url = source["url"].format(year=now.year)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != source["allowed_domain"]:
        raise ValueError(f"허용되지 않은 출처 URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "stock-sayo-study/1.0 research@example.invalid"})
    with urllib.request.urlopen(request, timeout=25, context=tls_context()) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.hostname != source["allowed_domain"]:
            raise ValueError(f"출처 리다이렉트 도메인 불일치: {response.geturl()}")
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("출처 응답 최대 크기 초과")
        return body, response.geturl()


def iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except (TypeError, ValueError):
            return None


def base_candidate(source: dict, title: str, url: str, published_at: str | None, excerpt: str = "") -> dict:
    return {
        "id": candidate_id(url, title),
        "source_id": source["id"],
        "title": title[:300],
        "source_url": url,
        "published_at": published_at,
        "published_at_quality": "direct" if published_at else "unknown",
        "source_family": source["source_family"],
        "candidate_segments": source["segments"],
        "candidate_pillar": source["default_pillar"],
        "direction": "unverified",
        "review_status": "pending",
        "content_excerpt": excerpt[:6000],
        "content_hash": hashlib.sha256(f"{title}\n{excerpt}".encode()).hexdigest(),
    }


def parse_rss(source: dict, body: bytes, final_url: str, limit: int) -> list[dict]:
    root = ET.fromstring(body)
    items = root.findall(".//item")
    atom = False
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        atom = True
    candidates = []
    for item in items[:limit]:
        if atom:
            title = (item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_node = item.find("{http://www.w3.org/2005/Atom}link")
            link = (link_node.get("href") if link_node is not None else final_url).strip()
            published_raw = item.findtext("{http://www.w3.org/2005/Atom}updated") or item.findtext("{http://www.w3.org/2005/Atom}published")
            excerpt = item.findtext("{http://www.w3.org/2005/Atom}summary") or ""
        else:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or final_url).strip()
            published_raw = item.findtext("pubDate")
            excerpt = item.findtext("description") or ""
        published = iso_date(published_raw)
        excerpt_parser = TextExtractor()
        excerpt_parser.feed(excerpt)
        clean_excerpt = " ".join(excerpt_parser.text_parts) or " ".join(excerpt.split())
        if title:
            candidates.append(base_candidate(source, title, link, published, clean_excerpt))
    return candidates


def parse_html_watch(source: dict, body: bytes, final_url: str) -> list[dict]:
    parser = TextExtractor()
    parser.feed(body.decode("utf-8", errors="replace"))
    title = " ".join(parser.title_parts) or source["label"]
    excerpt = " ".join(parser.text_parts)[:6000]
    candidate = base_candidate(source, title, final_url, None, excerpt)
    candidate["content_hash"] = hashlib.sha256(body).hexdigest()
    return [candidate]


def parse_page_date(value: str) -> date | None:
    formats = ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass
    return None


def parse_dated_html_watch(source: dict, body: bytes, final_url: str, observed: datetime) -> list[dict]:
    parser = TextExtractor()
    parser.feed(body.decode("utf-8", errors="replace"))
    text = " ".join(parser.text_parts)
    month = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    patterns = (rf"{month}\s+\d{{1,2}},\s+\d{{4}}", rf"\d{{1,2}}\s+{month}\s+\d{{4}}", r"\d{4}-\d{2}-\d{2}")
    dated = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            parsed = parse_page_date(match.group(0).title())
            if parsed and parsed <= observed.date() + timedelta(days=1):
                dated.append((parsed, match.start()))
    if not dated:
        return parse_html_watch(source, body, final_url)
    published, position = max(dated)
    excerpt = text[max(0, position - 300):position + 3700]
    title = (" ".join(parser.title_parts) or source["label"]) + f" · {published.isoformat()}"
    return [base_candidate(source, title, final_url, published.isoformat(), excerpt)]


def parse_tsmc(source: dict, body: bytes, final_url: str, observed: datetime) -> list[dict]:
    parser = TextExtractor()
    parser.feed(body.decode("utf-8", errors="replace"))
    text = " ".join(parser.text_parts)
    month = r"(?:Jan\.|Feb\.|Mar\.|Apr\.|May|Jun\.|Jul\.|Aug\.|Sept\.|Oct\.|Nov\.|Dec\.)"
    matches = re.findall(rf"({month})\s+([\d,]+)\s+([+-]?\d+(?:\.\d+)?)%", text)
    if not matches:
        return parse_html_watch(source, body, final_url)
    month_name, revenue, yoy = matches[-1]
    title = f"TSMC {observed.year} {month_name} monthly revenue"
    candidate = base_candidate(source, title, final_url, None, text[:6000])
    candidate.update({
        "fact_candidate": f"TSMC {month_name} consolidated revenue was {revenue} million TWD, YoY {yoy}%.",
        "numeric_values": {"revenue_million_twd": int(revenue.replace(",", "")), "yoy_pct": float(yoy)},
        "published_at_quality": "unknown",
    })
    return [candidate]


def main() -> None:
    now = datetime.now(KST)
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    output_path = ROOT / "data/inbox.json"
    previous = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else {}
    merged = {item["id"]: item for item in previous.get("candidates", []) if item.get("id")}
    source_results = []
    for source in registry["sources"]:
        try:
            body, final_url = fetch(source, now)
            if source["type"] == "rss":
                candidates = parse_rss(source, body, final_url, registry["max_items_per_source"])
            elif source["type"] == "tsmc_monthly_revenue":
                candidates = parse_tsmc(source, body, final_url, now)
            elif source["type"] == "dated_html_watch":
                candidates = parse_dated_html_watch(source, body, final_url, now)
            else:
                candidates = parse_html_watch(source, body, final_url)
            for candidate in candidates:
                candidate_url = urllib.parse.urlparse(candidate["source_url"])
                allowed_links = set(source.get("allowed_link_domains", [source["allowed_domain"]]))
                if candidate_url.scheme != "https" or candidate_url.hostname not in allowed_links:
                    continue
                candidate["collected_at"] = now.isoformat(timespec="seconds")
                merged[candidate["id"]] = {**merged.get(candidate["id"], {}), **candidate}
            source_results.append({"source_id": source["id"], "status": "ok", "items": len(candidates)})
        except Exception as exc:
            source_results.append({"source_id": source["id"], "status": "error", "error": str(exc)[:240]})

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "candidates": sorted(merged.values(), key=lambda item: item.get("collected_at") or "", reverse=True),
        "source_results": source_results,
    }
    atomic_write(output_path, payload)
    ok = sum(1 for item in source_results if item["status"] == "ok")
    print(f"출처 {ok}/{len(source_results)}개 수집 성공 · 후보 {len(payload['candidates'])}개")
    if ok == 0:
        raise RuntimeError("모든 출처 수집이 실패했습니다")


if __name__ == "__main__":
    main()
