"""Public macro observations and display context, independent of buy thresholds.

No credentials, Firebase, or environment files are used here. FRED observations
keep their actual dates; fetching an old release never makes it a new observation.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


KST = timezone(timedelta(hours=9))
US_TZ = ZoneInfo("America/New_York")
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SERIES = {
    "us_2y": {"series_id": "DGS2", "label": "미국 2년 국채금리", "unit": "%", "frequency": "daily", "publication_frequency": "daily", "max_lag_days": 3, "check_hours": 6},
    "us_real_10y": {"series_id": "DFII10", "label": "미국 10년 실질금리", "unit": "%", "frequency": "daily", "publication_frequency": "daily", "max_lag_days": 3, "check_hours": 6},
    # H.10 has daily observations published in a weekly release. It must not be
    # treated as stale just because there is no new observation each morning.
    "usd_broad": {"series_id": "DTWEXBGS", "label": "광의 달러지수", "unit": "index", "frequency": "daily", "publication_frequency": "weekly", "max_lag_days": 8, "check_hours": 24},
    "nfci": {"series_id": "NFCI", "label": "미국 금융여건", "unit": "index", "frequency": "weekly", "publication_frequency": "weekly", "max_lag_days": 10, "check_hours": 24},
    "initial_claims": {"series_id": "ICSA", "label": "미국 신규 실업수당", "unit": "people", "frequency": "weekly", "publication_frequency": "weekly", "max_lag_days": 8, "check_hours": 24},
    "core_cpi": {"series_id": "CPILFESL", "label": "미국 근원 CPI 지수", "unit": "index", "frequency": "monthly", "publication_frequency": "monthly", "max_lag_months": 2, "check_hours": 24},
    "core_pce": {"series_id": "PCEPILFE", "label": "미국 근원 PCE 지수", "unit": "index", "frequency": "monthly", "publication_frequency": "monthly", "max_lag_months": 2, "check_hours": 24},
    "unemployment": {"series_id": "UNRATE", "label": "미국 실업률", "unit": "%", "frequency": "monthly", "publication_frequency": "monthly", "max_lag_months": 2, "check_hours": 24},
}
CORE_KEYS = ("vix", "qqq", "kospi", "usdkrw")
GOOD_DATA_STATUSES = {"fresh", "market_closed"}
MAX_DOWNLOAD_BYTES = 2_000_000
MAX_EXPANDED_BYTES = 8_000_000


def iso(value: datetime) -> str:
    return value.astimezone(KST).isoformat(timespec="seconds")


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    return parsed if math.isfinite(parsed) else None


def observation_time(value):
    """Preserve provider date precision; never invent a midnight quote time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") if value.tzinfo else None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.isoformat(timespec="seconds") if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def business_days_between(start: date, end: date) -> int:
    """Weekdays excluding the observation day; release lags allow holidays.

    This is a release-lag budget, not a claim to be an exchange holiday calendar.
    The source date and session are always exposed for audit.
    """
    if end <= start:
        return 0
    days = (end - start).days
    weeks, remainder = divmod(days, 7)
    return weeks * 5 + sum((start + timedelta(days=weeks * 7 + i)).weekday() < 5 for i in range(1, remainder + 1))


def compact_history(points, limit=60):
    by_date = {}
    for point in points or []:
        if not isinstance(point, dict):
            continue
        stamp = observation_time(point.get("date"))
        value = number(point.get("value"))
        if stamp and value is not None:
            by_date[stamp[:10]] = round(value, 6)
    return [{"date": key, "value": by_date[key]} for key in sorted(by_date)[-limit:]]


def annotate_market_value(value, key, now=None, *, from_cache=False):
    now = now or datetime.now(KST)
    if not isinstance(value, dict):
        return None
    result = deepcopy(value)
    observed = observation_time(result.get("observed_at") or result.get("data_as_of"))
    source_now = now.astimezone(KST if key in {"kospi", "usdkrw"} else US_TZ)
    valid = number(result.get("current" if key in {"vix", "usdkrw"} else "price")) is not None
    source_date = date.fromisoformat(observed[:10]) if observed else None
    lag = business_days_between(source_date, source_now.date()) if source_date else None
    invalid_date = source_date is not None and source_date > source_now.date()
    if from_cache or result.get("is_stale"):
        status = "stale"
    elif not valid or observed is None or invalid_date:
        status = "unavailable"
    elif lag > 2:
        status = "stale"
    else:
        clock = source_now.time().replace(tzinfo=None)
        opens, closes = (time(9), time(15, 30)) if key == "kospi" else (time(9, 30), time(16))
        active = source_now.weekday() < 5 and opens <= clock < closes
        if source_date == source_now.date():
            status = "fresh" if active or key == "usdkrw" else "market_closed"
        elif source_now.weekday() >= 5 or clock < opens:
            # Allow a last completed weekday observation before the next open.
            # No weekday holiday is guessed: a closed exchange on a weekday
            # needs its calendar, otherwise this remains a delayed observation.
            expected = source_now.date() - timedelta(days=1)
            while expected.weekday() >= 5:
                expected -= timedelta(days=1)
            status = "market_closed" if source_date == expected else "delayed"
        else:
            status = "delayed"
    result["observed_at"] = observed
    result["data_as_of"] = observed
    result["observation_precision"] = "date" if observed and len(observed) == 10 else "time" if observed else "unknown"
    result["retrieved_at"] = iso(now)
    result["attempted_at"] = iso(now)
    result["data_status"] = status
    result["is_stale"] = status in {"stale", "unavailable", "delayed"}
    result["session"] = "KR_MARKET" if key == "kospi" else "FX_MARKET" if key == "usdkrw" else "US_MARKET"
    result["history"] = compact_history(result.get("history"))
    if not from_cache and valid and observed is not None and not invalid_date:
        result["last_success_at"] = iso(now)
    if from_cache:
        result["original_source"] = result.get("original_source") or result.get("source")
        result["source"] = "firestore_cache"
        # Legacy document-wide updated strings are not observation timestamps.
        result["stale_as_of"] = observed or result.get("stale_as_of")
    return result


def market_value_usable(value, number_key):
    if not isinstance(value, dict) or number(value.get(number_key)) is None:
        return False
    if value.get("is_stale") or value.get("data_status") in {"stale", "unavailable", "expired", "unknown", "delayed"}:
        return False
    return True


def _series_freshness(item, config, now):
    stamp = observation_time(item.get("observed_at"))
    observed = date.fromisoformat(stamp[:10]) if stamp else None
    today = now.astimezone(US_TZ).date()
    if number(item.get("current")) is None or observed is None or observed > today:
        return "unavailable"
    if config["frequency"] == "monthly":
        month_age = (today.year - observed.year) * 12 + today.month - observed.month
        return "stale" if month_age > config["max_lag_months"] else "fresh"
    return "stale" if business_days_between(observed, today) > config["max_lag_days"] else "fresh"


def _derived_changes(key, history):
    if len(history) < 2:
        return {}
    result = {}
    last = history[-1]
    for days, name in ((7, "change_1w"), (30, "change_1m")):
        cutoff = date.fromisoformat(last["date"]) - timedelta(days=days)
        previous = [point for point in history if date.fromisoformat(point["date"]) <= cutoff]
        if previous:
            result[name] = round(last["value"] - previous[-1]["value"], 6)
    if key == "initial_claims" and len(history) >= 4:
        recent = history[-4:]
        if all((date.fromisoformat(recent[index]["date"]) - date.fromisoformat(recent[index - 1]["date"])).days == 7 for index in range(1, 4)):
            result["four_week_average"] = round(sum(point["value"] for point in recent) / 4, 1)
    if key in {"core_cpi", "core_pce"} and len(history) >= 4:
        recent = history[-4:]
        months = [date.fromisoformat(point["date"]).year * 12 + date.fromisoformat(point["date"]).month for point in recent]
        if all(months[index] - months[index - 1] == 1 for index in range(1, 4)) and recent[0]["value"] > 0:
            result["trend_3m_annualized_pct"] = round(((recent[-1]["value"] / recent[0]["value"]) ** 4 - 1) * 100, 2)
            result["trend_method"] = "seasonally-adjusted-index-three-consecutive-month-change-annualized"
    return result


def parse_fred_csv(text, requested, now):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_EXPANDED_BYTES:
        raise ValueError("invalid public macro CSV")
    rows = csv.DictReader(io.StringIO(text))
    fields = rows.fieldnames or []
    date_key = next((key for key in fields if key.lower() in {"date", "observation_date"}), None)
    available = [key for key in requested if SERIES[key]["series_id"] in fields]
    if not date_key or not available:
        raise ValueError("public macro CSV columns missing")
    points = {key: [] for key in available}
    for row in rows:
        stamp = observation_time(row.get(date_key))
        if not stamp or len(stamp) != 10 or date.fromisoformat(stamp) > now.astimezone(US_TZ).date():
            continue
        for key in available:
            value = number(row.get(SERIES[key]["series_id"]))
            if value is not None:
                points[key].append({"date": stamp, "value": value})
    output = {}
    for key in available:
        config = SERIES[key]
        history = compact_history(points[key])
        if not history:
            continue
        item = {
            "id": key, "label": config["label"], "series_id": config["series_id"],
            "current": history[-1]["value"], "unit": config["unit"],
            "frequency": config["frequency"], "publication_frequency": config["publication_frequency"],
            "observed_at": history[-1]["date"],
            "observation_precision": "month" if config["frequency"] == "monthly" else "date",
            "reference_period": history[-1]["date"][:7] if config["frequency"] == "monthly" else history[-1]["date"],
            "published_at": None,
            "freshness_basis": "publication-cycle-lag-budget",
            "retrieved_at": iso(now), "last_success_at": iso(now), "last_checked_at": iso(now),
            "next_check_at": iso(now + timedelta(hours=config["check_hours"])),
            "source": "FRED", "source_url": f"https://fred.stlouisfed.org/series/{config['series_id']}",
            "history": history, "display_only": True,
            "last_attempt_status": "success",
            "seasonal_adjustment": "seasonally_adjusted" if key in {"core_cpi", "core_pce", "unemployment", "initial_claims"} else "not_seasonally_adjusted",
            **_derived_changes(key, history),
        }
        item["data_status"] = _series_freshness(item, config, now)
        item["is_stale"] = item["data_status"] != "fresh"
        if len(history) > 1:
            item["change"] = round(history[-1]["value"] - history[-2]["value"], 6)
            item["change_unit"] = "percentage_points" if config["unit"] == "%" else "people" if config["unit"] == "people" else "index_points"
        output[key] = item
    return output


def parse_fred_payload(payload, requested, now):
    """Accept FRED's CSV or mixed-frequency ZIP without extracting any files."""
    if not isinstance(payload, bytes) or len(payload) > MAX_DOWNLOAD_BYTES:
        raise ValueError("public macro download size exceeded")
    if payload.startswith(b"PK"):
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
            entries = archive.infolist()
            if len(entries) > 16 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("public macro archive size exceeded")
            output = {}
            for entry in entries:
                if entry.is_dir() or not entry.filename.lower().endswith(".csv"):
                    continue
                if entry.flag_bits & 1:
                    raise ValueError("encrypted public macro archive")
                body = archive.read(entry)
                try:
                    parsed = parse_fred_csv(body.decode("utf-8-sig"), requested, now)
                except (ValueError, UnicodeDecodeError, csv.Error):
                    continue
                for key, item in parsed.items():
                    if key in output:
                        raise ValueError("duplicate series in public macro archive")
                    output[key] = item
            if not output:
                raise ValueError("public macro archive has no requested observations")
            return output
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError("invalid public macro archive") from error
    try:
        return parse_fred_csv(payload.decode("utf-8-sig"), requested, now)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("invalid public macro CSV") from error


def _bounded_response_bytes(response):
    """Bound decompressed HTTP bytes before interpreting CSV/archive members."""
    result = bytearray()
    try:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=64_000):
            if not isinstance(chunk, bytes):
                raise ValueError("invalid public macro response bytes")
            result.extend(chunk)
            if len(result) > MAX_DOWNLOAD_BYTES:
                raise ValueError("public macro download size exceeded")
    finally:
        response.close()
    return bytes(result)


def collect_reference_series(previous=None, now=None, fetcher=None, on_error=None):
    """At most one bounded, keyless FRED CSV/ZIP request per attempt."""
    now = now or datetime.now(KST)
    previous = previous or {}
    result, due = {}, []
    for key, config in SERIES.items():
        old = deepcopy(previous.get(key) or {})
        checked = _datetime(old.get("last_checked_at"))
        check_hours = 1 if old.get("last_attempt_status") == "failed" else config["check_hours"]
        if old and checked and timedelta(0) <= now - checked < timedelta(hours=check_hours):
            old["data_status"] = _series_freshness(old, config, now)
            if old.get("last_attempt_status") == "failed":
                old["data_status"] = "stale" if number(old.get("current")) is not None else "unavailable"
            old["is_stale"] = old["data_status"] != "fresh"
            result[key] = old
        else:
            due.append(key)
    if not due:
        return result
    request_failed = False
    try:
        if fetcher is None:
            import requests
            fetcher = requests.get
        response = fetcher(
            FRED_CSV_URL,
            params={"id": ",".join(SERIES[key]["series_id"] for key in due), "cosd": (now.date() - timedelta(days=180)).isoformat()},
            timeout=12,
            stream=True,
        )
        fresh = parse_fred_payload(_bounded_response_bytes(response), due, now)
    except Exception as error:
        # Callers publish only error categories, never URLs or response bodies.
        if on_error:
            on_error(error)
        request_failed = True
        fresh = {}
    for key in due:
        if key in fresh:
            result[key] = fresh[key]
            continue
        old = deepcopy(previous.get(key) or {})
        old.update({
            "id": key, "label": SERIES[key]["label"], "unit": SERIES[key]["unit"],
            "frequency": SERIES[key]["frequency"], "publication_frequency": SERIES[key]["publication_frequency"],
            "source_url": f"https://fred.stlouisfed.org/series/{SERIES[key]['series_id']}",
            "last_checked_at": iso(now),
            # Retry an unavailable series after an hour, not every 30-minute run.
            "next_check_at": iso(now + timedelta(hours=1)),
            "data_status": "stale" if number(old.get("current")) is not None else "unavailable",
            "is_stale": True, "display_only": True,
            "last_attempt_status": "failed",
        })
        old["history"] = compact_history(old.get("history"))
        result[key] = old
        if on_error and not request_failed:
            on_error(ValueError("public macro series unavailable"))
    return result


def build_market_environment(core, series, now=None):
    now = now or datetime.now(KST)
    combined = {**core, **series}
    quality = {key: (combined.get(key) or {}).get("data_status", "unavailable") for key in (*CORE_KEYS, *SERIES)}
    unavailable = [key for key, state in quality.items() if state not in GOOD_DATA_STATUSES]
    axes = [
        {"id": "market", "label": "시장 변동", "series_ids": ["vix", "qqq", "kospi"], "summary": "VIX와 지역 지수의 관측·추세를 확인합니다."},
        {"id": "rates", "label": "금리 부담", "series_ids": ["us_2y", "us_real_10y"], "summary": "정책 기대와 실질 할인율을 구분합니다. 상승 자체를 기업 실적 악화로 단정하지 않습니다."},
        {"id": "credit", "label": "금융 여건", "series_ids": ["nfci"], "summary": "주간 금융 여건을 확인합니다. 0보다 높으면 장기 평균보다 긴축적인 여건입니다."},
        {"id": "fx", "label": "환율", "series_ids": ["usdkrw", "usd_broad"], "summary": "원·달러 환율과 광의 달러 흐름을 함께 비교합니다."},
        {"id": "inflation", "label": "물가 추세", "series_ids": ["core_cpi", "core_pce"], "summary": "근원 물가지수와 연속 월간 자료의 3개월 연율을 구분합니다. 수정치는 다음 수집에서 재확인합니다."},
        {"id": "employment", "label": "고용·경기", "series_ids": ["initial_claims", "unemployment"], "summary": "신규 실업수당의 4주 평균과 월간 실업률을 함께 봅니다. 서로 다른 발표 주기를 유지합니다."},
    ]
    for axis in axes:
        axis["status"] = "available" if all(quality.get(key) in GOOD_DATA_STATUSES for key in axis["series_ids"]) else "pending"
    return {
        "schema_version": "1.0", "generated_at": iso(now), "display_only": True,
        "quality_status": "complete" if not unavailable else "partial" if any(state in GOOD_DATA_STATUSES for state in quality.values()) else "unavailable",
        "summary": "시장·금리·금융 여건·환율·물가·고용을 구분해 확인합니다." if not unavailable else f"일부 거시 자료 {len(unavailable)}개는 직전값 또는 확인 대기 상태입니다.",
        "unavailable_series": unavailable, "axes": axes, "series": series,
        "assessment_policy": "reference-only-no-buy-score-or-threshold-changes",
    }
