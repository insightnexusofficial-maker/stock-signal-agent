import firebase_admin
from firebase_admin import credentials, firestore
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import pandas as pd
import re
import os
import time
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from config import (
    KR_STOCKS, US_STOCKS, KR_ETFS,
    SECTOR_CRITERIA, TREND_WINDOWS, MACRO_GUARDS,
    SLOPE_RULES, BUY_LEVELS, EXIT_TRIGGERS,
)

load_dotenv()

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Snapshot 캐시 (실행당 1번만 조회)
_snapshot_cache = {}
_read_counter = {"calls": 0, "cache_hits": 0, "actual_reads": 0}
_published_payload_cache = None



APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

KST = timezone(timedelta(hours=9))


# ============================================================
# 공통 유틸
# ============================================================
def calculate_rsi(prices, period=14):
    """표준 RSI (Wilder smoothing)."""
    if len(prices) < period + 1:
        return None
    
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0)
        losses.append(abs(change) if change < 0 else 0)
    
    # 1) 첫 14일 단순 평균
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # 2) 15일째부터 Wilder smoothing (EMA-style)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def get_date_str():
    return datetime.now(KST).strftime("%Y%m%d")


def get_month_str():
    return datetime.now(KST).strftime("%Y%m")


def _avg(values):
    """None 제외 평균."""
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _slope_pct(curr, base):
    """(curr/base - 1) * 100 (%). base가 None/0이면 None."""
    if curr is None or base is None or base == 0:
        return None
    try:
        return round(float((curr / base - 1) * 100), 2)
    except (TypeError, ValueError):
        return None


def _sanitize_for_firestore(obj):
    """numpy 타입 → Python 네이티브로 재귀 변환."""
    if hasattr(obj, 'item') and hasattr(obj, 'dtype'):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return None
        return float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_firestore(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_firestore(x) for x in obj]
    return obj


def _to_float(value):
    """쉼표/단위가 섞인 숫자를 float로 변환. 실패/NaN은 None."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            cleaned = re.sub(r"[^0-9.\-]", "", value)
            if cleaned in ("", "-", ".", "-."):
                return None
            value = cleaned
        num = float(value)
        if num != num:
            return None
        return num
    except (TypeError, ValueError):
        return None


def calculate_forward_peg(forward_pe, expected_eps_growth_pct):
    """Forward P/E를 예상 EPS 성장률(퍼센트 단위)로 나눈 표준 PEG."""
    pe = _to_float(forward_pe)
    growth = _to_float(expected_eps_growth_pct)
    if pe is None or growth is None or pe <= 0 or growth <= 0:
        return None
    return round(pe / growth, 2)


def calculate_cagr(start_value, end_value, years):
    """양수 EPS 구간의 연환산 성장률(CAGR)을 퍼센트 단위로 반환한다."""
    start = _to_float(start_value)
    end = _to_float(end_value)
    years = _to_float(years)
    if start is None or end is None or years is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return round(((end / start) ** (1 / years) - 1) * 100, 1)


def _normalize_growth_pct(value):
    """Yahoo 성장률처럼 소수/퍼센트 표기가 섞인 값을 퍼센트 단위로 맞춘다."""
    growth = _to_float(value)
    if growth is None:
        return None
    if -1 < growth < 1:
        growth *= 100
    return round(growth, 1)


def _get_row_values(table, label):
    for row in table.select("tr"):
        header = row.select_one("th")
        if not header:
            continue
        if label in header.get_text(" ", strip=True):
            return [_to_float(td.get_text(" ", strip=True)) for td in row.select("td")]
    return []


def _extract_target_distribution_yield(text):
    if not text:
        return None
    patterns = (
        r"타겟\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        r"목표\s*(?:연\s*)?([0-9]+(?:\.[0-9]+)?)\s*%",
        r"연\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:분배|배당)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _to_float(match.group(1))
    return None


def _has_valid_rate(value):
    return _to_float(value) is not None


def _make_exchange_rate(current, prev=None, change_pct=None, source=None):
    """환율 공급원별 응답을 Firestore 공통 형식으로 정규화한다."""
    current = _to_float(current)
    prev = _to_float(prev)
    change_pct = _to_float(change_pct)
    if current is None or current <= 0:
        return None
    if prev is None and change_pct is not None and change_pct > -100:
        prev = current / (1 + change_pct / 100)
    if change_pct is None and prev is not None and prev > 0:
        change_pct = (current - prev) / prev * 100
    return {
        "current": round(current, 2),
        "prev": round(prev, 2) if prev is not None else round(current, 2),
        "change_pct": round(change_pct, 2) if change_pct is not None else 0,
        "source": source,
    }

# ============================================================
# 매크로 지표 (VIX, QQQ, KOSPI, USD/KRW)
# ============================================================
def _calc_ma_status(hist, period=20, buffer_pct=0.01, confirm_days=2):
    """MA 이탈 판정 (whipsaw 완충 포함)."""
    if hist is None or hist.empty or len(hist) < period:
        return None
    
    closes = hist["Close"].dropna()
    if len(closes) < period:
        return None
    current_price = float(closes.iloc[-1])
    ma_current = float(closes.iloc[-period:].mean())
    deviation_pct = round((current_price - ma_current) / ma_current * 100, 2)
    
    if len(closes) < period + confirm_days - 1:
        return {
            "price": round(current_price, 2),
            "ma20": round(ma_current, 2),
            "above_ma20": current_price > ma_current,
            "deviation_pct": deviation_pct,
        }
    
    below_confirmed = True
    for i in range(confirm_days):
        idx_end = len(closes) - i
        idx_start = idx_end - period
        if idx_start < 0:
            below_confirmed = False
            break
        price_i = float(closes.iloc[idx_end - 1])
        ma_i = float(closes.iloc[idx_start:idx_end].mean())
        if price_i >= ma_i * (1 - buffer_pct):
            below_confirmed = False
            break
    
    return {
        "price": round(current_price, 2),
        "ma20": round(ma_current, 2),
        "above_ma20": not below_confirmed,
        "deviation_pct": deviation_pct,
    }


def _get_usdkrw_from_naver_api():
    url = (
        "https://m.stock.naver.com/front-api/marketIndex/productDetail"
        "?category=exchange&reutersCode=FX_USDKRW"
    )
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    res.raise_for_status()
    payload = res.json()
    data = payload.get("result") or payload.get("data") or {}

    current = data.get("closePrice") or data.get("currentPrice") or data.get("now")
    change_pct = (
        data.get("fluctuationsRatio")
        or data.get("fluctuationRate")
        or data.get("changeRate")
    )
    change = _to_float(
        data.get("compareToPreviousClosePrice")
        or data.get("changePrice")
        or data.get("change")
    )
    direction = data.get("compareToPreviousPrice") or data.get("direction") or {}
    if isinstance(direction, dict):
        direction = " ".join(str(direction.get(key, "")) for key in ("text", "name", "code"))
    if change is not None and any(word in str(direction).lower() for word in ("하락", "falling", "down")):
        change = -abs(change)
    current_num = _to_float(current)
    prev = current_num - change if current_num is not None and change is not None else None
    return _make_exchange_rate(current_num, prev, change_pct, "naver_mobile")


def _get_usdkrw_from_yahoo_chart():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/KRW=X"
    params = {"range": "1mo", "interval": "1d"}
    res = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    res.raise_for_status()
    chart = (res.json().get("chart") or {}).get("result") or []
    if not chart:
        return None
    closes = (((chart[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    closes = [_to_float(value) for value in closes]
    closes = [value for value in closes if value is not None]
    if not closes:
        return None
    current = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else current
    return _make_exchange_rate(current, prev, source="yahoo_chart")


def _get_usdkrw_from_naver_html():
    url = "https://finance.naver.com/marketindex/"
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    block = soup.select_one("#exchangeList .head_info, .market1 .head_info")
    if not block:
        return None
    value_el = block.select_one(".value")
    change_el = block.select_one(".change")
    if not value_el or not change_el:
        return None
    current = _to_float(value_el.get_text(" ", strip=True))
    change = _to_float(change_el.get_text(" ", strip=True))
    direction_text = block.get_text(" ", strip=True)
    if current is None or change is None:
        return None
    if "하락" in direction_text:
        change = -abs(change)
    prev = current - change
    return _make_exchange_rate(current, prev, source="naver_html")


def _get_published_payload():
    global _published_payload_cache
    if _published_payload_cache is None:
        snapshot = db.collection("stocks").document("data").get()
        _published_payload_cache = snapshot.to_dict() if snapshot.exists else {}
    return _published_payload_cache or {}


def _get_cached_macro_value(field):
    """외부 공급원이 모두 실패해도 직전 정상값을 지우지 않는다."""
    try:
        payload = _get_published_payload()
        value = payload.get(field)
        required_key = "current" if field in ("vix", "usdkrw") else "price"
        if not isinstance(value, dict) or _to_float(value.get(required_key)) is None:
            return None
        cached = deepcopy(value)
        cached["source"] = "firestore_cache"
        cached["is_stale"] = True
        cached["stale_as_of"] = payload.get("updated")
        return cached
    except Exception as e:
        print(f"   {field} 직전값 조회 에러: {e}")
        return None


def get_macro_data():
    result = {"vix": None, "qqq": None, "kospi": None, "usdkrw": None}
    
    buf = MACRO_GUARDS.get("qqq_whipsaw_buffer", 0.01)
    confirm = MACRO_GUARDS.get("qqq_confirm_days", 2)
    
    # VIX
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="1mo")
        closes = hist["Close"].dropna() if not hist.empty else []
        if len(closes):
            current = round(float(closes.iloc[-1]), 2)
            ma5 = round(float(closes.iloc[-5:].mean()), 2) if len(closes) >= 5 else current
            prev = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else current
            
            if current < 25:
                mode = "normal"
            elif current < 35:
                mode = "level1"
            else:
                mode = "level2"
            
            reversal = current < ma5 and prev >= ma5
            
            result["vix"] = {
                "current": current,
                "ma5": ma5,
                "mode": mode,
                "reversal": reversal,
            }
    except Exception as e:
        print(f"   VIX 에러: {e}")
    
    # QQQ
    try:
        qqq = yf.Ticker("QQQ")
        hist = qqq.history(period="2mo")
        status = _calc_ma_status(
            hist,
            period=MACRO_GUARDS.get("qqq_ma_period", 20),
            buffer_pct=buf,
            confirm_days=confirm,
        )
        if status:
            result["qqq"] = status
    except Exception as e:
        print(f"   QQQ 에러: {e}")
    
    # KOSPI
    try:
        kospi = yf.Ticker(MACRO_GUARDS.get("kospi_ticker", "^KS11"))
        hist = kospi.history(period="2mo")
        status = _calc_ma_status(
            hist,
            period=MACRO_GUARDS.get("kospi_ma_period", 20),
            buffer_pct=buf,
            confirm_days=confirm,
        )
        if status:
            result["kospi"] = status
    except Exception as e:
        print(f"   KOSPI 에러: {e}")
    
    # 환율 (USD/KRW): HTML 구조나 단일 공급원 장애에 묶이지 않도록 순차 폴백.
    rate_providers = (
        ("네이버 모바일", _get_usdkrw_from_naver_api),
        ("Yahoo chart", _get_usdkrw_from_yahoo_chart),
        ("네이버 HTML", _get_usdkrw_from_naver_html),
    )
    for provider_name, provider in rate_providers:
        try:
            result["usdkrw"] = provider()
            if result["usdkrw"]:
                break
        except Exception as e:
            print(f"   USD/KRW {provider_name} 에러: {e}")

    for field in ("vix", "qqq", "kospi", "usdkrw"):
        if not result[field]:
            result[field] = _get_cached_macro_value(field)
        
    return result

    


def determine_market_mode(macro, region="us"):
    vix_data = macro.get("vix") or {}
    idx_data = macro.get("kospi") if region == "kr" else macro.get("qqq")
    idx_data = idx_data or {}
    
    vix_current = vix_data.get("current", 0)
    idx_above = idx_data.get("above_ma20", True)
    
    # 우선순위: panic > caution > adjust > normal
    if vix_current >= 35:
        return "panic"
    elif vix_current >= 25:
        return "caution"
    elif vix_current >= 20 or not idx_above:
        return "adjust"
    else:
        return "normal"


def get_rsi_threshold(criteria, market_mode):
    key = f"rsi_{market_mode}"
    return criteria.get(key, criteria.get("rsi_normal", 40))


# ============================================================
# 스냅샷 저장/조회
# ============================================================
def save_snapshot(code, data):
    clean_data = {
        key: value for key, value in data.items()
        if not _is_missing_value(value)
    }
    meaningful = [
        value for key, value in clean_data.items()
        if key not in ("data_as_of", "is_stale", "stale_as_of", "stale_filled_fields")
    ]
    if not meaningful:
        print(f"   스냅샷 생략 ({code}): 유효 데이터 없음")
        return
    
    date_str = get_date_str()
    doc_ref = db.collection("snapshots").document(date_str).collection("stocks").document(code)
    # 공급원 일부가 실패한 실행에서 None으로 같은 날의 정상 필드를 지우지 않는다.
    doc_ref.set(_sanitize_for_firestore(clean_data), merge=True)
    for key in list(_snapshot_cache.keys()):
        if key.startswith(f"{code}_"):
            del _snapshot_cache[key]

def get_snapshot_history(code, days=35):
    _read_counter["calls"] += 1
    cache_key = f"{code}_{days}"
    if cache_key in _snapshot_cache:
        _read_counter["cache_hits"] += 1
        return _snapshot_cache[cache_key]
    
    snapshots = []
    base_date = datetime.now(KST)
    
    for i in range(days):
        target_date = base_date - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        
        try:
            doc = db.collection("snapshots").document(date_str).collection("stocks").document(code).get()
            _read_counter["actual_reads"] += 1
            if doc.exists:
                data = doc.to_dict()
                data["date"] = date_str
                snapshots.append(data)
        except:
            pass
    
    _snapshot_cache[cache_key] = snapshots
    return snapshots


def _is_missing_value(value):
    return value is None or value == ""


def get_latest_valid_snapshot(code, days=35, required_fields=("price",)):
    """최근 유효 스냅샷. 휴장/주말 빈 응답 시 마지막 장중 데이터를 유지하는 용도."""
    snapshots = get_snapshot_history(code, days)
    snapshots.sort(key=lambda x: x["date"], reverse=True)
    
    for snapshot in snapshots:
        if any(not _is_missing_value(snapshot.get(field)) for field in required_fields):
            return snapshot
    return None


def merge_missing_from_snapshot(code, data, fields, required_fields=("price",)):
    """각 누락 필드를 서로 다른 날짜의 최근 정상 스냅샷으로 보완한다."""
    snapshots = get_snapshot_history(code, 35)
    snapshots.sort(key=lambda snapshot: snapshot.get("date", ""), reverse=True)
    if not snapshots:
        return data
    
    filled = []
    field_sources = {}
    for field in fields:
        if not _is_missing_value(data.get(field)):
            continue
        for snapshot in snapshots:
            if _is_missing_value(snapshot.get(field)):
                continue
            data[field] = snapshot.get(field)
            filled.append(field)
            field_sources[field] = snapshot.get("data_as_of") or snapshot.get("date")
            break
    
    if filled:
        data["is_stale"] = True
        source_dates = [value for value in field_sources.values() if value]
        data["stale_as_of"] = max(source_dates) if source_dates else None
        data["stale_filled_fields"] = filled
        data["stale_field_sources"] = field_sources
    return data


def dedupe_records(records, code_key="code", name_key="name"):
    """동일 코드 또는 정규화한 동일 이름의 중복 종목을 첫 항목만 남긴다."""
    unique = []
    seen_codes = set()
    seen_names = set()
    for record in records:
        code = str(record.get(code_key) or "").strip().casefold()
        name = re.sub(r"\s+", "", str(record.get(name_key) or "")).casefold()
        if (code and code in seen_codes) or (name and name in seen_names):
            print(f"   중복 종목 제거: {record.get(name_key)} ({record.get(code_key)})")
            continue
        if code:
            seen_codes.add(code)
        if name:
            seen_names.add(name)
        unique.append(record)
    return unique


KR_STOCK_SNAPSHOT_FIELDS = (
    "price", "rsi", "volume", "vol_avg_20", "vol_ratio",
    "price_source", "price_provider_gap_pct", "price_market_status",
    "eps_fwd", "eps_ttm", "eps_growth", "eps_growth_raw", "eps_growth_source", "eps_growth_quality",
    "annual_eps_prev", "annual_eps_fwd", "annual_eps_growth", "annual_eps_cagr", "annual_eps_cagr_years",
    "peg_growth_rate", "peg_growth_horizon",
    "peg_fwd", "peg_raw", "pbr", "per_fwd", "per_ttm", "div_yield", "dps", "bps",
    "peg_source", "peg_quality", "annual_per_fwd", "annual_pbr_fwd",
    "target_price", "target_gap", "investment_opinion_score",
    "per_source", "eps_source", "target_price_source", "trailing_source",
)

KR_ETF_SNAPSHOT_FIELDS = (
    "price", "rsi", "volume", "vol_avg_20", "vol_ratio",
    "nav", "nav_discount", "band_pct",
    "distribution_yield_ttm", "distribution_yield_monthly",
    "distribution_target_yield_annual", "distribution_target_yield_monthly",
    "distribution_target_source", "distribution_target_checked_month",
    "expected_dividend_5m", "expected_monthly_dividend_5m", "expected_dividend_source",
)


# ============================================================
# 기울기 계산 (EPS + 목표주가 공통 일반화)
# ============================================================
def _calculate_trend_generic(code, field_name):
    snapshots = get_snapshot_history(code, 35)
    snapshots = [
        snapshot for snapshot in snapshots
        if not _is_missing_value(snapshot.get(field_name))
    ]
    if not snapshots:
        return {}
    
    snapshots.sort(key=lambda x: x["date"], reverse=True)
    
    raw_today = snapshots[0].get(field_name) if snapshots else None
    raw_yesterday = snapshots[1].get(field_name) if len(snapshots) >= 2 else None
    raw_change_pct = _slope_pct(raw_today, raw_yesterday)
    if len(snapshots) < 2:
        return {
            "v_curr": raw_today,
            "v_wow": None,
            "v_mom": None,
            "slope_wow_pct": None,
            "slope_mom_pct": None,
            "raw_today": raw_today,
            "raw_yesterday": None,
            "raw_change_pct": None,
            "wow_violated": False,
            "mom_violated": False,
        }
    
    cw = TREND_WINDOWS["curr_days"]
    wo = TREND_WINDOWS["wow_offset"]
    wd = TREND_WINDOWS["wow_days"]
    mo = TREND_WINDOWS["mom_offset"]
    md = TREND_WINDOWS["mom_days"]
    
    v_curr = _avg([s.get(field_name) for s in snapshots[:cw]])
    v_wow = _avg([s.get(field_name) for s in snapshots[wo:wo + wd]])
    v_mom = _avg([s.get(field_name) for s in snapshots[mo:mo + md]])
    
    slope_wow_pct = _slope_pct(v_curr, v_wow)
    slope_mom_pct = _slope_pct(v_curr, v_mom)
    
    wow_violated = bool(v_curr is not None and v_wow is not None and v_curr < v_wow)
    mom_violated = bool(v_curr is not None and v_mom is not None and v_curr < v_mom)
    
    return {
        "v_curr": v_curr,
        "v_wow": v_wow,
        "v_mom": v_mom,
        "slope_wow_pct": slope_wow_pct,
        "slope_mom_pct": slope_mom_pct,
        "raw_today": raw_today,
        "raw_yesterday": raw_yesterday,
        "raw_change_pct": raw_change_pct,
        "wow_violated": wow_violated,
        "mom_violated": mom_violated,
    }


def _judge_trend_state(slope_mom_pct, slope_wow_pct):
    """4단계: accelerating / decelerating / reversing / declining / unknown"""
    if slope_mom_pct is None or slope_wow_pct is None:
        return "unknown"
    if slope_mom_pct > 0 and slope_wow_pct < 0:
        return "reversing"
    if slope_mom_pct <= 0 and slope_wow_pct <= 0:
        return "declining"
    if slope_mom_pct > 0 and slope_wow_pct > slope_mom_pct:
        return "accelerating"
    if slope_mom_pct > 0 and slope_wow_pct <= slope_mom_pct:
        return "decelerating"
    return "unknown"


def calculate_eps_trend(code):
    return _calculate_trend_generic(code, "eps_fwd")


def calculate_target_trend(code):
    return _calculate_trend_generic(code, "target_price")


# ============================================================
# 국내 주식 (KIS API + FnGuide)
# ============================================================
def get_kis_token():
    if not APP_KEY or not APP_SECRET:
        raise RuntimeError("KIS_APP_KEY 또는 KIS_APP_SECRET이 없습니다")
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(url, headers={"content-type": "application/json"}, json=body, timeout=12)
    res.raise_for_status()
    payload = res.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(payload.get("error_description") or payload.get("msg1") or "KIS 토큰 응답에 access_token이 없습니다")
    return token


def _get_kis_json(url, headers, params, label, attempts=3):
    """KIS의 일시적 호출 제한은 재시도하고 최종 실패는 종목 누락 대신 빈 응답으로 돌린다."""
    for attempt in range(1, attempts + 1):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=12)
            res.raise_for_status()
            payload = res.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == attempts:
                print(f"   KIS {label} 요청 에러: {e}")
                return {}
            time.sleep(0.7 * attempt)
            continue

        if payload.get("rt_cd") == "0":
            return payload

        message = payload.get("msg1") or payload.get("message") or "알 수 없는 오류"
        code = payload.get("msg_cd") or payload.get("error_code") or payload.get("rt_cd")
        if attempt == attempts:
            print(f"   KIS {label} 실패 [{code}]: {message}")
            return payload
        time.sleep(0.7 * attempt)
    return {}


def get_naver_stock_quote(code):
    """네이버 모바일의 정규장 현재가를 국내 시세 독립 검증값으로 사용한다."""
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    res = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=10,
    )
    res.raise_for_status()
    payload = res.json()
    price = _to_float(payload.get("closePrice"))
    if price is None or price <= 0:
        return {}
    return {
        "price": int(price),
        "price_source": "naver_mobile_realtime",
        "price_market_status": payload.get("marketStatus"),
    }


def get_kr_stock_data(token, code):
    result = {}
    if token:
        headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST01010100"}
        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        payload = _get_kis_json(url, headers, params, f"현재가 {code}")
        if payload.get("rt_cd") == "0":
            output = payload.get("output") or {}
            result["price"] = int(output["stck_prpr"]) if output.get("stck_prpr") else None
            result["volume"] = int(output.get("acml_vol", 0) or 0)
            result["price_source"] = "kis_realtime"

        time.sleep(0.5)

        headers["tr_id"] = "FHKST03010100"
        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        today = datetime.now(KST).strftime("%Y%m%d")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": today, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
        payload = _get_kis_json(url, headers, params, f"일봉 {code}")
        if payload.get("rt_cd") == "0":
            candles = [d for d in (payload.get("output2") or []) if d.get("stck_clpr")]
            prices = [int(d["stck_clpr"]) for d in reversed(candles)]
            result["rsi"] = calculate_rsi(prices)

            if candles:
                if not result.get("price"):
                    result["price"] = int(candles[0]["stck_clpr"])
                    result["price_source"] = "kis_latest_close"
                result["volume"] = result.get("volume") or int(candles[0].get("acml_vol", 0) or 0)

            if len(candles) >= 20:
                vol_20 = [int(d.get("acml_vol", 0)) for d in candles[:20]]
                result["vol_avg_20"] = sum(vol_20) / 20
                if result["vol_avg_20"] > 0 and result.get("volume") is not None:
                    result["vol_ratio"] = round(result["volume"] / result["vol_avg_20"], 2)

    # KIS 인증/호출 장애에도 가격을 확보하고, 두 공급원이 다르면 사용자 기준 시세인
    # 네이버 정규장 현재가를 우선한다.
    try:
        naver_quote = get_naver_stock_quote(code)
        naver_price = naver_quote.get("price")
        kis_price = result.get("price")
        if naver_price and kis_price:
            result["price_provider_gap_pct"] = round((kis_price - naver_price) / naver_price * 100, 2)
        if naver_price:
            result.update(naver_quote)
    except Exception as e:
        print(f"   네이버 현재가 에러 ({code}): {e}")
    
    return result


def get_naver_consensus(code):
    """
    네이버 증권의 투자의견/목표주가 및 추정 PER/EPS 컨센서스.
    추정 EPS는 네이버 페이지 안내상 FnGuide 컨센서스(증권사 3곳 이상) 기반이다.
    """
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    res = requests.get(url, headers=headers, timeout=12)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    result = {}
    
    opinion_table = soup.find("table", attrs={"summary": "투자의견 정보"})
    if opinion_table:
        for row in opinion_table.select("tr"):
            header = row.select_one("th")
            if not header:
                continue
            if "투자의견" in header.get_text(" ", strip=True) and "목표주가" in header.get_text(" ", strip=True):
                ems = [_to_float(em.get_text(" ", strip=True)) for em in row.select("td em")]
                ems = [v for v in ems if v is not None]
                if ems:
                    result["investment_opinion_score"] = ems[0]
                if len(ems) >= 2 and ems[1] > 0:
                    result["target_price"] = ems[1]
                    result["target_price_source"] = "naver_consensus"
                break
    
    cns_per = _to_float(soup.select_one("#_cns_per").get_text(" ", strip=True)) if soup.select_one("#_cns_per") else None
    cns_eps = _to_float(soup.select_one("#_cns_eps").get_text(" ", strip=True)) if soup.select_one("#_cns_eps") else None
    if cns_per is not None and cns_per > 0:
        result["per_fwd"] = cns_per
        result["per_source"] = "naver_consensus"
    if cns_eps is not None and cns_eps > 0:
        result["eps_fwd"] = cns_eps
        result["eps_source"] = "naver_consensus"
    
    per_ttm = _to_float(soup.select_one("#_per").get_text(" ", strip=True)) if soup.select_one("#_per") else None
    eps_ttm = _to_float(soup.select_one("#_eps").get_text(" ", strip=True)) if soup.select_one("#_eps") else None
    pbr = _to_float(soup.select_one("#_pbr").get_text(" ", strip=True)) if soup.select_one("#_pbr") else None
    if per_ttm is not None and per_ttm > 0:
        result["naver_per_ttm"] = per_ttm
    if eps_ttm is not None and eps_ttm > 0:
        result["naver_eps_ttm"] = eps_ttm
    if pbr is not None and pbr > 0:
        result["naver_pbr"] = pbr
    
    finance_table = None
    for table in soup.select("table"):
        if table.select_one("th.th_cop_anal17"):
            finance_table = table
            break
    
    if finance_table:
        eps_values = _get_row_values(finance_table, "EPS")
        per_values = _get_row_values(finance_table, "PER")
        pbr_values = _get_row_values(finance_table, "PBR")
        bps_values = _get_row_values(finance_table, "BPS")
        dps_values = _get_row_values(finance_table, "주당배당금")
        div_values = _get_row_values(finance_table, "시가배당률")
        
        if len(eps_values) >= 4:
            prev_eps = eps_values[2]
            fwd_eps = eps_values[3]
            result["annual_eps_prev"] = prev_eps
            result["annual_eps_fwd"] = fwd_eps
            if prev_eps and fwd_eps and prev_eps != 0:
                result["annual_eps_growth"] = round((fwd_eps - prev_eps) / abs(prev_eps) * 100, 1)

            # 네이버는 장기 성장률을 직접 제공하지 않는다. 최근 3개 확정치 중
            # 가장 오래된 양수 EPS부터 다음 연도 컨센서스까지의 CAGR을 사용해
            # 단년도 턴어라운드 기저효과를 완화한다.
            if fwd_eps and fwd_eps > 0:
                for index, base_eps in enumerate(eps_values[:3]):
                    years = 3 - index
                    cagr = calculate_cagr(base_eps, fwd_eps, years)
                    if cagr is not None:
                        result["annual_eps_cagr"] = cagr
                        result["annual_eps_cagr_years"] = years
                        break
        
        if len(per_values) >= 4 and per_values[3] and per_values[3] > 0:
            result["annual_per_fwd"] = per_values[3]
        if len(pbr_values) >= 4 and pbr_values[3] and pbr_values[3] > 0:
            result["annual_pbr_fwd"] = pbr_values[3]
        if len(bps_values) >= 4 and bps_values[3] and bps_values[3] > 0:
            result["annual_bps_fwd"] = bps_values[3]
        if len(dps_values) >= 3 and dps_values[2] and dps_values[2] > 0:
            result["annual_dps_latest"] = dps_values[2]
        if len(div_values) >= 3 and div_values[2] and div_values[2] > 0:
            result["annual_div_yield_latest"] = div_values[2]
    
    return result


def get_kr_valuation(code):
    from datetime import datetime, timedelta
    
    result = {}
    stock = None
    if os.getenv("SAYO_USE_PYKRX") == "1":
        try:
            from pykrx import stock
        except (ImportError, RuntimeError) as e:
            print(f"   pykrx 사용 불가 ({code}), 네이버 폴백 사용: {e}")
    
    # 1) pykrx: PER/PBR/EPS/BPS (최근 14일 범위 조회 후 마지막 유효값 사용)
    if stock is not None:
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
            df = stock.get_market_fundamental_by_date(start, end, code)
            if not df.empty:
                latest = df.iloc[-1]
                result["per_ttm"] = _to_float(latest.get("PER"))
                result["pbr"] = _to_float(latest.get("PBR"))
                result["eps_ttm"] = _to_float(latest.get("EPS"))
                result["div_yield"] = _to_float(latest.get("DIV"))
                result["dps"] = _to_float(latest.get("DPS"))
                result["bps"] = _to_float(latest.get("BPS"))
                result["trailing_source"] = "pykrx"
        except Exception as e:
            print(f"   pykrx fundamental 에러 ({code}): {e}")
    
    # 2) EPS 성장률 (작년 vs 올해 동일 시점 비교)
    if stock is not None:
        try:
            today = datetime.now()
            this_year_date = today.strftime("%Y%m%d")
            last_year_date = (today - timedelta(days=365)).strftime("%Y%m%d")

            # 최근 유효 거래일 찾기 위해 앞뒤 10일 범위로 조회
            def get_nearest_eps(target_date_str):
                target = datetime.strptime(target_date_str, "%Y%m%d")
                start = (target - timedelta(days=10)).strftime("%Y%m%d")
                end = (target + timedelta(days=10)).strftime("%Y%m%d")
                df = stock.get_market_fundamental_by_date(start, end, code)
                if df.empty:
                    return None
                return _to_float(df["EPS"].iloc[-1])

            eps_now = get_nearest_eps(this_year_date)
            eps_prev = get_nearest_eps(last_year_date)

            if eps_now and eps_prev and eps_prev != 0:
                result["eps_growth"] = round((eps_now - eps_prev) / abs(eps_prev) * 100, 1)
        except Exception as e:
            print(f"   EPS 성장률 계산 에러 ({code}): {e}")
    
    # 3) 네이버 증권: 목표주가/추정 PER/EPS 컨센서스.
    # comp.fnguide.com SVD_Main.asp는 gicode 유실 리다이렉트가 있어 사용하지 않는다.
    try:
        result.update(get_naver_consensus(code))
    except Exception as e:
        print(f"   네이버 컨센서스 에러 ({code}): {e}")
    
    if result.get("per_ttm") is None:
        result["per_ttm"] = result.get("naver_per_ttm")
    if result.get("eps_ttm") is None:
        result["eps_ttm"] = result.get("naver_eps_ttm")
    if result.get("pbr") is None:
        result["pbr"] = result.get("naver_pbr")
    if result.get("bps") is None:
        result["bps"] = result.get("annual_bps_fwd")
    if result.get("dps") is None:
        result["dps"] = result.get("annual_dps_latest")
    if result.get("div_yield") is None:
        result["div_yield"] = result.get("annual_div_yield_latest")
    if result.get("trailing_source") is None and any(result.get(k) is not None for k in ("per_ttm", "eps_ttm", "pbr")):
        result["trailing_source"] = "naver_trailing_fallback"
    
    if result.get("eps_growth") is None and result.get("annual_eps_growth") is not None:
        result["eps_growth"] = result["annual_eps_growth"]
        result["eps_growth_raw"] = result["annual_eps_growth"]
        result["eps_growth_source"] = "naver_annual_consensus_yoy"
        if not (5 <= result["eps_growth"] <= 200):
            result["eps_growth_quality"] = "extreme_growth_not_for_signal"
        else:
            result["eps_growth_quality"] = "normal"
    elif result.get("eps_growth") is None and result.get("eps_fwd") and result.get("eps_ttm"):
        eps_growth = round((result["eps_fwd"] - result["eps_ttm"]) / abs(result["eps_ttm"]) * 100, 1)
        result["eps_growth"] = eps_growth
        result["eps_growth_raw"] = eps_growth
        result["eps_growth_source"] = "naver_consensus_vs_trailing"
        if 5 <= eps_growth <= 200:
            result["eps_growth_quality"] = "normal"
        else:
            result["eps_growth_quality"] = "extreme_growth_not_for_signal"
    
    if result.get("per_fwd") is None:
        result["per_fwd"] = result.get("annual_per_fwd") or result.get("per_ttm")
        result["per_source"] = "naver_annual_consensus" if result.get("annual_per_fwd") else "pykrx_trailing_fallback"
    if result.get("eps_fwd") is None:
        result["eps_fwd"] = result.get("annual_eps_fwd") or result.get("eps_ttm")
        result["eps_source"] = "naver_annual_consensus" if result.get("annual_eps_fwd") else "pykrx_trailing_fallback"
    
    if result.get("eps_growth") is not None and result.get("eps_growth_quality") is None:
        if 5 <= result["eps_growth"] <= 200:
            result["eps_growth_quality"] = "normal"
        else:
            result["eps_growth_quality"] = "extreme_growth_not_for_signal"
            result["eps_growth_raw"] = result["eps_growth"]

    # 국내 장기 전망치가 별도로 없으므로 가장 긴 양수 EPS 구간 CAGR을 우선한다.
    # 이 값은 3~5년 순수 forward 전망이 아니라 확정치+전망치 혼합 대용치임을 보존한다.
    peg_growth = result.get("annual_eps_cagr") or result.get("eps_growth")
    peg_years = result.get("annual_eps_cagr_years")
    result["peg_growth_rate"] = peg_growth
    result["peg_growth_horizon"] = f"{peg_years}y_mixed_cagr_proxy" if peg_years else "1y_forward"
    peg = calculate_forward_peg(result.get("per_fwd"), peg_growth)
    if peg is not None:
        result["peg_raw"] = peg
        result["peg_fwd"] = peg
        result["peg_source"] = (
            f"naver_annual_consensus_{peg_years}y_mixed_cagr"
            if peg_years else result.get("eps_growth_source")
        )
        if peg_growth is None or not (5 <= peg_growth <= 100):
            result["peg_quality"] = "high_growth_base_effect"
        else:
            result["peg_quality"] = "normal_proxy"
    
    return result


# ============================================================
# 미국 주식 (yfinance) + Earnings Surprise
# ============================================================
def get_us_earnings_surprise(stock):
    try:
        eh = stock.earnings_history
        if eh is None or eh.empty:
            return None
        latest = eh.iloc[-1]
        sp = latest.get("surprisePercent")
        if sp is None:
            return None
        sp = float(sp)
        if sp != sp:  # NaN
            return None
        if -1 < sp < 1:
            sp = sp * 100
        return round(sp, 2)
    except Exception:
        return None


def get_us_forward_eps_growth(stock, info):
    """PEG 비교 가능성을 위해 Yahoo 장기 전망을 우선한다."""
    try:
        estimates = stock.growth_estimates
        if isinstance(estimates, pd.DataFrame):
            for horizon, source in (
                ("+5y", "yahoo_growth_estimates_5y"),
                ("+1y", "yahoo_growth_estimates_1y"),
            ):
                if horizon not in estimates.index:
                    continue
                growth = _normalize_growth_pct(estimates.loc[horizon].get("stockTrend"))
                if growth is not None and growth > 0:
                    return growth, source
    except Exception:
        pass

    forward_eps = _to_float(info.get("forwardEps"))
    trailing_eps = _to_float(info.get("trailingEps"))
    if forward_eps is not None and trailing_eps is not None and trailing_eps > 0:
        growth = (forward_eps - trailing_eps) / trailing_eps * 100
        if growth > 0:
            return round(growth, 1), "yahoo_forward_vs_trailing_eps"

    earnings_growth = _to_float(info.get("earningsGrowth"))
    if earnings_growth is not None and earnings_growth > 0:
        return round(earnings_growth * 100, 1), "yahoo_earnings_growth_fallback"
    return None, None


def get_us_stock_data(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    result = {}
    
    result["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
    result["per_fwd"] = info.get("forwardPE")
    result["per_ttm"] = info.get("trailingPE")
    result["pbr"] = info.get("priceToBook")
    result["ps"] = info.get("priceToSalesTrailing12Months")
    result["eps_fwd"] = info.get("forwardEps")
    result["eps_ttm"] = info.get("trailingEps")
    
    eps_growth, eps_growth_source = get_us_forward_eps_growth(stock, info)
    if eps_growth is not None:
        result["eps_growth"] = eps_growth
        result["eps_growth_source"] = eps_growth_source
    
    rg = info.get("revenueGrowth")
    if rg is not None:
        result["rev_growth"] = round(rg * 100, 1)
    
    result["peg_growth_rate"] = result.get("eps_growth")
    result["peg_growth_horizon"] = "5y_forward" if eps_growth_source == "yahoo_growth_estimates_5y" else "1y_forward"
    peg = calculate_forward_peg(result.get("per_fwd"), result.get("eps_growth"))
    if peg is not None:
        result["peg_fwd"] = peg
        result["peg_raw"] = peg
        result["peg_source"] = eps_growth_source
        result["peg_quality"] = "normal"
    
    target = info.get("targetMeanPrice")
    if target and result["price"]:
        result["target_gap"] = round((target - result["price"]) / result["price"] * 100, 1)
        result["target_price"] = target
    
    result["earnings_surprise_pct"] = get_us_earnings_surprise(stock)
    
    hist = stock.history(period="3mo")
    if not hist.empty:
        result["rsi"] = calculate_rsi(hist["Close"].tolist())
        result["volume"] = int(hist["Volume"].iloc[-1])
        
        if len(hist) >= 20:
            vol_avg = hist["Volume"].iloc[-20:].mean()
            if vol_avg > 0:
                result["vol_ratio"] = round(result["volume"] / vol_avg, 2)
        
        low52 = info.get("fiftyTwoWeekLow")
        high52 = info.get("fiftyTwoWeekHigh")
        if low52 and high52 and high52 > low52:
            result["band_pct"] = round((result["price"] - low52) / (high52 - low52) * 100, 1)
    
    return result


# ============================================================
# 국내 ETF
# ============================================================
def get_naver_etf_metrics(code):
    result = {}
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/integration"
        res = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
            timeout=10,
        )
        res.raise_for_status()
        payload = res.json()

        if payload.get("stockName"):
            result["name"] = payload["stockName"]

        target_source_text = ""
        total_infos = payload.get("totalInfos") or []
        has_one_year_return = False
        for item in total_infos:
            key = item.get("code")
            if key == "etfBaseIdx":
                target_source_text = f"{target_source_text} {item.get('value') or ''}"
            elif key == "oneYearEarnRate":
                has_one_year_return = _has_valid_rate(item.get("value"))
            val = _to_float(item.get("value"))
            if val is None:
                continue
            if key == "accumulatedTradingVolume":
                result["volume"] = int(val)
            elif key == "nav":
                result["nav"] = val

        stock_name = payload.get("stockName") or ""
        description = payload.get("description") or ""
        target_source_text = f"{target_source_text} {description}"
        is_covered_call = "커버드콜" in f"{stock_name} {description}"
        target_yield = _extract_target_distribution_yield(target_source_text)
        if target_yield is not None and target_yield > 0:
            result["distribution_target_yield_annual"] = target_yield
            result["distribution_target_yield_monthly"] = round(target_yield / 12, 2)
            result["distribution_target_source"] = "naver_etf_description"
            result["distribution_target_checked_month"] = get_month_str()

        deal_infos = payload.get("dealTrendInfos") or []
        if deal_infos:
            latest = deal_infos[0]
            price = _to_float(latest.get("closePrice"))
            volume = _to_float(latest.get("accumulatedTradingVolume"))
            if price is not None:
                result["price"] = price
            if volume is not None and result.get("volume") is None:
                result["volume"] = int(volume)

        key_indicator = payload.get("etfKeyIndicator") or {}
        if _to_float(key_indicator.get("returnRate1y")) is not None:
            has_one_year_return = True
        nav = _to_float(key_indicator.get("nav"))
        if nav is not None:
            result["nav"] = nav
        deviation = _to_float(key_indicator.get("deviationRate"))
        if deviation is not None:
            sign = key_indicator.get("deviationSign")
            result["nav_discount"] = round(-deviation if sign == "-" else deviation, 2)
        use_recent_monthly_distribution = False
        distribution_yield = _to_float(key_indicator.get("dividendYieldTtm"))
        if distribution_yield is not None and distribution_yield > 0:
            result["distribution_yield_ttm"] = distribution_yield
            use_recent_monthly_distribution = (
                is_covered_call
                and not result.get("distribution_target_yield_annual")
                and not has_one_year_return
                and distribution_yield < 6
            )
            monthly_distribution_yield = distribution_yield / 12
            if use_recent_monthly_distribution:
                monthly_distribution_yield = distribution_yield
            result["distribution_yield_monthly"] = round(monthly_distribution_yield, 2)
            result["expected_dividend_5m"] = int(round(5_000_000 * distribution_yield / 100, 0))

        if result.get("distribution_target_yield_annual"):
            expected_monthly_yield = result["distribution_target_yield_annual"] / 12
            result["expected_dividend_source"] = "target_distribution_yield"
        else:
            expected_monthly_yield = result.get("distribution_yield_monthly")
            if expected_monthly_yield is not None:
                result["expected_dividend_source"] = (
                    "recent_monthly_distribution_yield"
                    if use_recent_monthly_distribution
                    else "ttm_distribution_yield"
                )
        if expected_monthly_yield is not None and expected_monthly_yield > 0:
            result["expected_monthly_dividend_5m"] = int(round(5_000_000 * expected_monthly_yield / 100, 0))
    except Exception as e:
        print(f"   네이버 ETF 지표 에러 ({code}): {e}")
    return result


def apply_configured_etf_distribution_target(etf, result):
    """설정에 명시한 목표 분배율을 월 기준 계산값으로 적용한다."""
    monthly_yield = _to_float(etf.get("distribution_target_yield_monthly"))
    annual_yield = _to_float(etf.get("distribution_target_yield_annual"))
    if monthly_yield is not None and monthly_yield > 0:
        annual_yield = monthly_yield * 12
    elif annual_yield is not None and annual_yield > 0:
        monthly_yield = annual_yield / 12
    else:
        return result

    result["distribution_target_yield_annual"] = round(annual_yield, 2)
    result["distribution_target_yield_monthly"] = round(monthly_yield, 2)
    result["distribution_target_source"] = "config_override"
    result["distribution_target_checked_month"] = get_month_str()
    result["expected_dividend_source"] = "target_distribution_yield"
    result["expected_dividend_5m"] = int(round(5_000_000 * annual_yield / 100, 0))
    result["expected_monthly_dividend_5m"] = int(round(5_000_000 * monthly_yield / 100, 0))
    return result


def get_etf_data(etf):
    ticker = etf["ticker_yf"]
    result = {"name": etf["name"], "code": etf["ticker_krx"]}
    naver_metrics = get_naver_etf_metrics(etf["ticker_krx"])
    
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="3mo")
        if not hist.empty:
            result["price"] = round(float(hist["Close"].iloc[-1]), 0)
            result["volume"] = int(hist["Volume"].iloc[-1])
            if len(hist) >= 15:
                result["rsi"] = calculate_rsi(hist["Close"].tolist())

            if len(hist) >= 20:
                vol_avg = hist["Volume"].iloc[-20:].mean()
                if vol_avg > 0:
                    result["vol_avg_20"] = float(vol_avg)
                    result["vol_ratio"] = round(result["volume"] / vol_avg, 2)

            info = stock.info
            low52 = info.get("fiftyTwoWeekLow")
            high52 = info.get("fiftyTwoWeekHigh")
            if low52 and high52 and high52 > low52:
                result["band_pct"] = round((result["price"] - low52) / (high52 - low52) * 100, 1)
        
    except Exception as e:
        print(f"   ETF 에러 ({etf['name']}): {e}")

    for key, value in naver_metrics.items():
        if value is not None and (
            result.get(key) is None
            or key in (
                "nav",
                "nav_discount",
                "distribution_yield_ttm",
                "distribution_yield_monthly",
                "distribution_target_yield_annual",
                "distribution_target_yield_monthly",
                "distribution_target_source",
                "distribution_target_checked_month",
                "expected_dividend_5m",
                "expected_monthly_dividend_5m",
                "expected_dividend_source",
            )
        ):
            result[key] = value
    
    try:
        url = f"https://finance.naver.com/item/sise.naver?code={etf['ticker_krx']}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res.encoding = "euc-kr"
        
        idx = res.text.find(">NAV<")
        if idx >= 0:
            chunk = res.text[idx:idx + 2000]
            tds = re.findall(r"<td[^>]*>(.*?)</td>", chunk, re.DOTALL)
            td_values = [re.sub(r"<[^>]+>", "", td).strip() for td in tds if re.sub(r"<[^>]+>", "", td).strip()]
            if td_values:
                nav = float(td_values[0].replace(",", ""))
                result["nav"] = nav
                result["nav_discount"] = round((result["price"] - nav) / nav * 100, 2)
    except: pass
    
    apply_configured_etf_distribution_target(etf, result)

    # 상장 전 시세가 없어도 설정된 커버드콜 목표 분배금은 계산기에 노출한다.
    if result.get("price") is None and result.get("expected_monthly_dividend_5m") is None:
        return None
    if result.get("nav") and result.get("price") and result.get("nav_discount") is None:
        result["nav_discount"] = round((result["price"] - result["nav"]) / result["nav"] * 100, 2)

    return result

# ============================================================
# 매수 시그널 판정 (3단계: candidate / strong / now)
# ============================================================
def check_stock_signal(data, sector, macro, region="us"):
    """
    매수 시그널 판정.
    
    반환:
        step1 (bool): 기초 체력 통과 (매수 후보 자격)
        step2 (bool): RSI 매수 구간 안 (STEP1 통과 + zone 진입)
        reason (str|None): 탈락 사유 (빠른 탈락 시)
    
    data에 직접 기록되는 필드:
        market_mode, rsi_threshold
        eps_trend_state, target_trend_state
        trend_*, target_*  (기울기 상세)
        buy_level: 'none' / 'candidate' / 'strong'
        in_buy_zone: bool
        selection_hits: int (선택 조건 충족 개수)
    """
    criteria = SECTOR_CRITERIA.get(sector, {})
    market_mode = determine_market_mode(macro, region=region)
    rsi_threshold = get_rsi_threshold(criteria, market_mode)
    
    data["market_mode"] = market_mode
    data["rsi_threshold"] = rsi_threshold
    data["buy_level"] = "none"
    data["in_buy_zone"] = False
    data["selection_hits"] = 0
    
    # === 기울기 계산 (EPS + 목표주가) ===
    eps_trend = {}
    target_trend = {}
    if data.get("code"):
        eps_trend = calculate_eps_trend(data["code"])
        target_trend = calculate_target_trend(data["code"])
    
    for k, v in eps_trend.items():
        data[f"trend_{k}"] = v
    for k, v in target_trend.items():
        data[f"target_{k}"] = v
    
    data["eps_trend_state"] = _judge_trend_state(
        eps_trend.get("slope_mom_pct"),
        eps_trend.get("slope_wow_pct"),
    )
    data["target_trend_state"] = _judge_trend_state(
        target_trend.get("slope_mom_pct"),
        target_trend.get("slope_wow_pct"),
    )
    
    # === 지표 변수 정리 (거래량 체크는 hits 계산 후로 연기) ===
    rsi = data.get("rsi")
    peg = data.get("peg_fwd")
    peg_for_signal = None if data.get("peg_quality") == "high_growth_base_effect" else peg
    pbr = data.get("pbr")
    per = data.get("per_ttm") or data.get("per_fwd")
    per_fwd = data.get("per_fwd")
    ps = data.get("ps")
    rev_growth = data.get("rev_growth")
    eps_growth = data.get("eps_growth")
    eps_growth_for_signal = None if data.get("eps_growth_quality") == "extreme_growth_not_for_signal" else eps_growth
    band_pct = data.get("band_pct")
    earnings_surprise = data.get("earnings_surprise_pct")
    target_gap = data.get("target_gap")
    div_yield = data.get("div_yield")
    
    # === EPS 기울기 필수 조건 (매우 관대: slope_mom ≥ -1%) ===
    slope_mom = eps_trend.get("slope_mom_pct")
    # 섹터별 임계값 (사이클 섹터는 0 이상 강화, 그 외 -1.0 관대)
    slope_required = criteria.get("slope_mom_min", SLOPE_RULES.get("required_mom_min", -1.0))
    # 데이터 부족 시 유예 (초기 도입 단계)
    eps_trend_ok = slope_mom is None or slope_mom >= slope_required
    
    # === 섹터별 필수 valuation 체크 ===
    val_ok = False
    
    if sector == "semiconductor":
        val_ok = peg_for_signal is not None and peg_for_signal < criteria.get("peg_max", 0.8)
    
    elif sector == "ai_bigtech":
        val_ok = peg_for_signal is not None and peg_for_signal < criteria.get("peg_max", 1.2)
    
    elif sector == "defense":
        peg_ok = peg_for_signal is not None and peg_for_signal < criteria.get("peg_max", 1.5)
        per_ok = per is not None and per < criteria.get("per_max", 15)
        val_ok = peg_ok or per_ok
    
    elif sector == "aerospace":
        eps_fwd = data.get("eps_fwd", 0) or 0
        if eps_fwd > 0:
            val_ok = peg_for_signal is not None and peg_for_signal < criteria.get("peg_max", 1.5)
        else:
            ps_ok = ps is not None and ps < criteria.get("ps_max", 10)
            band_ok = band_pct is not None and band_pct < criteria.get("band_max", 30)
            val_ok = ps_ok and band_ok
    
    elif sector == "industrial":
        # 산업재/중공업: 사이클 종목. PBR 제외, PER < 12 (저점 진입 기준)
        peg_ok = peg_for_signal is not None and peg_for_signal < criteria.get("peg_max", 1.2)
        per_ok = per is not None and per < criteria.get("per_max", 12)
        val_ok = peg_ok or per_ok
    
    elif sector == "growth":
        # 흑자/적자 + PEG 데이터 유무로 분기
        eps_fwd = data.get("eps_fwd", 0) or 0
        peg_available = peg_for_signal is not None
        
        if eps_fwd > 0 and peg_available:
            # 흑자 + PEG 정상 → PEG 기준
            val_ok = peg_for_signal < criteria.get("peg_max", 1.5)
        else:
            # 적자거나 PEG 못 잡은 경우 → 4중 fallback (모두 충족)
            ps_ok = ps is not None and ps < criteria.get("ps_max", 5)
            band_ok = band_pct is not None and band_pct < criteria.get("band_max", 30)
            surprise_ok = (earnings_surprise is not None 
                           and earnings_surprise >= criteria.get("fallback_surprise_min", 5))
            target_ok = (target_gap is not None 
                         and target_gap >= criteria.get("fallback_target_gap_min", 20))
            val_ok = ps_ok and band_ok and surprise_ok and target_ok
    
    if region == "kr" and not val_ok and peg_for_signal is None:
        per_fallback_max = criteria.get("kr_per_fallback_max")
        per_source = data.get("per_source")
        if (
            per_fallback_max is not None
            and per_fwd is not None
            and per_source == "naver_consensus"
            and per_fwd < per_fallback_max
        ):
            val_ok = True
            data["valuation_basis"] = "kr_consensus_per_fallback"
    
    # === 선택 조건 체크 ===
    hits = 0
    hit_details = []
    
    if region == "kr":
        # 한국 주식은 rev_growth/earnings_surprise가 안정적으로 제공되지 않는다.
        # Valuation 게이트는 별도로 유지하고, 국내에서 확보 가능한 보조 조건으로 hits를 구성한다.
        target_min = criteria.get("target_gap_min", 0)
        if target_gap is not None and target_gap >= target_min:
            hits += 1
            hit_details.append("target_gap")
        
        eps_growth_min = criteria.get("kr_eps_growth_min", criteria.get("rev_growth_min", 5))
        if eps_growth_min is not None and eps_growth_for_signal is not None and eps_growth_for_signal >= eps_growth_min:
            hits += 1
            hit_details.append("eps_growth")
        
        per_fallback_max = criteria.get("kr_per_fallback_max")
        if (
            per_fallback_max is not None
            and per_fwd is not None
            and data.get("per_source") == "naver_consensus"
            and per_fwd < per_fallback_max
        ):
            hits += 1
            hit_details.append("consensus_per")
        
        pbr_max = criteria.get("kr_pbr_max")
        if pbr_max is not None and pbr is not None and 0 < pbr <= pbr_max:
            hits += 1
            hit_details.append("pbr")
        
        div_min = criteria.get("kr_div_yield_min")
        if div_min is not None and div_yield is not None and div_yield >= div_min:
            hits += 1
            hit_details.append("div_yield")
    else:
        # 선택 1: 매출 성장률
        rev_min = criteria.get("rev_growth_min")
        if rev_min is not None and rev_growth is not None and rev_growth >= rev_min:
            hits += 1
            hit_details.append("rev_growth")
        
        # 선택 2: Earnings Surprise
        surprise_min = criteria.get("consensus_gap_min")
        if surprise_min is not None and earnings_surprise is not None and earnings_surprise >= surprise_min:
            hits += 1
            hit_details.append("earnings_surprise")
        
        # 선택 3: 목표주가 갭
        target_min = criteria.get("target_gap_min", 0)
        if target_gap is not None and target_gap >= target_min:
            hits += 1
            hit_details.append("target_gap")
    
    data["selection_hits"] = hits
    data["selection_hit_details"] = hit_details
    
    # === 유동성 체크 (hits 계산 후, step1 판정 전) ===
    vol_ratio = data.get("vol_ratio", 0) or 0
    vol_min = criteria.get("volume_min_ratio", 1.0)
    vol_ok = vol_ratio >= vol_min
    
    # === Step 1 통과 판정 ===
    step1 = val_ok and eps_trend_ok and hits >= 2
    
    # === RSI 매수 구간 판정 ===
    zone_upper = rsi_threshold + BUY_LEVELS.get("candidate_rsi_upper_offset", 10)
    in_zone = rsi is not None and rsi_threshold <= rsi <= zone_upper
    data["in_buy_zone"] = in_zone
    
    # === 매수 레벨 판정 (candidate / strong) ===
    # candidate: 펀더멘털 통과 (RSI 무관) — 관심 종목 워치리스트
    # strong:    candidate + 이익/목표가 가속 + RSI 매수 구간 안
    buy_level = "none"
    
    if step1:
        buy_level = "candidate"  # 🟢 매수 후보 (RSI 무관)
        
        # 가점: EPS slope > +3% AND 목표주가 slope > +3% AND RSI 매수 구간
        eps_bonus_min = SLOPE_RULES.get("bonus_mom_min", 3.0)
        eps_bonus = (slope_mom is not None and slope_mom > eps_bonus_min)
        target_slope_mom = target_trend.get("slope_mom_pct")
        target_bonus = (target_slope_mom is not None and target_slope_mom > eps_bonus_min)
        
        if eps_bonus and target_bonus and in_zone:
            buy_level = "strong"  # 🟢🟢 강력 매수 (RSI 적정 + 이익 가속)
    
    data["buy_level"] = buy_level
    
    # Step 2: 매수 구간 + 펀더멘털 통과 → "지금 매수" 후보 (notifier에서 돌파 감지)
    step2 = in_zone and step1
    return step1, step2, None


# ============================================================
# ETF 매수 시그널
# ============================================================
def check_etf_signal(data, macro):
    criteria = SECTOR_CRITERIA["etf"]
    market_mode = determine_market_mode(macro, region="kr")
    rsi_threshold = get_rsi_threshold(criteria, market_mode)
    
    rsi = data.get("rsi")
    nav_discount = data.get("nav_discount")
    band_pct = data.get("band_pct")
    
    # 매수 구간 판정
    zone_upper = rsi_threshold + BUY_LEVELS.get("candidate_rsi_upper_offset", 10)
    rsi_in_zone = rsi is not None and rsi <= zone_upper          # 임계값+10 이하 = 워치 구간
    rsi_strong = rsi is not None and rsi <= rsi_threshold        # 임계값 이하 = 강한 매수
    nav_ok = nav_discount is not None and nav_discount < criteria["nav_discount_threshold"]
    band_ok = band_pct is not None and band_pct < criteria["band_threshold"]

    hit_details = []
    if rsi_in_zone:
        hit_details.append("rsi_watch")
    if nav_ok:
        hit_details.append("nav_discount")
    if band_ok:
        hit_details.append("band_position")
    
    data["market_mode"] = market_mode
    data["rsi_threshold"] = rsi_threshold
    data["nav_discount_threshold"] = criteria["nav_discount_threshold"]
    data["band_threshold"] = criteria["band_threshold"]
    data["selection_hits"] = len(hit_details)
    data["selection_hit_details"] = hit_details
    data["in_buy_zone"] = rsi_strong  # notifier가 RSI 돌파 감지용
    
    # === 매수 레벨 판정 ===
    # candidate: RSI 워치 구간 OR NAV 할인 OR 밴드 하단
    # strong:    candidate + RSI 임계값 이하 (실제 과매도)
    buy_level = "none"
    
    if rsi_in_zone or nav_ok or band_ok:
        buy_level = "candidate"
        if rsi_strong and (nav_ok or band_ok):
            buy_level = "strong"
    
    data["buy_level"] = buy_level
    
    step1 = buy_level != "none"
    step2 = rsi_strong  # notifier에서 "RSI 돌파" 감지용
    
    return step1, step2, None


# ============================================================
# EPS 기울기 기반 정보성 알림 (매도 아님, 참고용)
# ============================================================
def check_exit_signal(data):
    """
    반환:
        level: 'info_mild' / 'info_warn' / 'info_watch' / None
        reasons: list[str]
    """
    reasons = []
    level = None
    
    eps_state = data.get("eps_trend_state", "unknown")
    target_state = data.get("target_trend_state", "unknown")
    slope_mom = data.get("trend_slope_mom_pct")
    slope_wow = data.get("trend_slope_wow_pct")
    raw_change = data.get("trend_raw_change_pct")
    
    # EPS raw 일별 급락 (-3% 이상)
    raw_limit = SLOPE_RULES.get("raw_drop_pct", -3.0)
    if raw_change is not None and raw_change <= raw_limit:
        level = "info_warn"
        reasons.append(f"EPS 컨센 raw 급락 ({raw_change:.1f}%)")
    
    # EPS 지속 하락
    if eps_state == "declining":
        level = "info_watch"
        if slope_mom is not None:
            reasons.append(f"EPS 지속 하락 (1M {slope_mom:.1f}%)")
        else:
            reasons.append("EPS 지속 하락")
    
    # EPS 방향 전환 (큰 흐름 플러스지만 단기 꺾임)
    elif eps_state == "reversing":
        if level is None:
            level = "info_warn"
        if slope_wow is not None:
            reasons.append(f"EPS 단기 꺾임 (1W {slope_wow:.1f}%)")
        else:
            reasons.append("EPS 단기 꺾임")
    
    # EPS 개선 완만 (정보성 mild)
    elif eps_state == "decelerating":
        if level is None:
            level = "info_mild"
        if slope_mom is not None and slope_wow is not None:
            reasons.append(f"EPS 성장 완화 (1M {slope_mom:.1f}% / 1W {slope_wow:.1f}%)")
    
    # 목표주가와 EPS 동시 하락 → 강한 경고
    if eps_state == "declining" and target_state == "declining":
        level = "info_watch"
        reasons.append("목표주가도 동반 하락")
    
    return level, reasons


# ============================================================
# 3대 위기 트리거 (자동 매도 알림 조건)
# ============================================================
def check_crisis_trigger(data, macro):
    """
    ① 기업 위기: EPS slope_mom < -5% AND 목표주가 slope_mom < -3% 
                AND (매출 마이너스 or 서프 -10% 이하)
    ② 시장 위기: VIX 40+ AND 지수 MA20 하향
    
    반환:
        triggers (list[str]): 발동된 트리거 ID
        details (list[str]): 상세 사유
    """
    triggers = []
    details = []
    
    # === ① 기업 위기 ===
    eps_slope = data.get("trend_slope_mom_pct")
    target_slope = data.get("target_slope_mom_pct")
    rev_growth = data.get("rev_growth")
    earnings_surprise = data.get("earnings_surprise_pct")
    
    crisis_eps = SLOPE_RULES.get("crisis_eps_mom", -5.0)
    crisis_target = SLOPE_RULES.get("crisis_target_mom", -3.0)
    
    eps_crisis = eps_slope is not None and eps_slope < crisis_eps
    target_crisis = target_slope is not None and target_slope < crisis_target
    rev_crisis = rev_growth is not None and rev_growth < 0
    surprise_crisis = earnings_surprise is not None and earnings_surprise <= -10
    
    if eps_crisis and target_crisis and (rev_crisis or surprise_crisis):
        triggers.append("company_crisis")
        parts = [f"EPS {eps_slope:.1f}%", f"목표 {target_slope:.1f}%"]
        if rev_crisis:
            parts.append(f"매출 {rev_growth:.1f}%")
        if surprise_crisis:
            parts.append(f"서프 {earnings_surprise:.1f}%")
        details.append("기업 위기: " + " / ".join(parts))
    
    # === ② 시장 위기 ===
    vix_data = macro.get("vix") or {}
    vix_current = vix_data.get("current", 0)
    vix_panic = EXIT_TRIGGERS.get("crisis_vix_panic", 40)
    
    code = data.get("code", "")
    is_us = code.isalpha() if code else False
    
    idx_data = macro.get("qqq") if is_us else macro.get("kospi")
    idx_data = idx_data or {}
    idx_below_ma = not idx_data.get("above_ma20", True)
    
    if vix_current >= vix_panic and idx_below_ma:
        triggers.append("market_panic")
        details.append(f"시장 위기: VIX {vix_current} + 지수 MA20 하향")
    
    return triggers, details


def publish_payload_patch(payload):
    """긴 수집 작업의 앞 단계 결과를 먼저 공개해 뒷 단계 장애의 영향을 격리한다."""
    safe_payload = {}
    protected_lists = {"kr_stock", "kr_etf", "us_stock"}
    for key, value in payload.items():
        if value is None:
            print(f"   마지막 정상값 유지: {key} (새 값 없음)")
            continue
        if key in protected_lists and isinstance(value, list) and not value:
            print(f"   마지막 정상값 유지: {key} (빈 배열)")
            continue
        safe_payload[key] = value
    if not safe_payload:
        return
    sanitized = _sanitize_for_firestore(safe_payload)
    db.collection("stocks").document("data").set(sanitized, merge=True)
    # 구버전 수집기가 stocks/data를 통째로 덮어써도 정상값을 복원할 수 있게
    # 별도 문서에 마지막 성공 결과를 유지한다.
    db.collection("stocks").document("last_good").set(sanitized, merge=True)

# ============================================================
# 메인 업로드 함수
# ============================================================
def upload_data():
    print("📊 데이터 수집 시작...")
    
    print("\n📈 매크로 지표 확인...")
    macro = get_macro_data()
    
    vix_info = macro.get("vix")
    qqq_info = macro.get("qqq")
    kospi_info = macro.get("kospi")
    usdkrw_info = macro.get("usdkrw")

    
    if vix_info:
        print(f"   VIX: {vix_info['current']} | 모드: 🟢 {vix_info['mode'].upper()}")
    if qqq_info:
        arrow = "🟢 상승" if qqq_info["above_ma20"] else "🔴 하락"
        print(f"   QQQ: ${qqq_info['price']} | MA20: ${qqq_info['ma20']} ({qqq_info['deviation_pct']}%) | {arrow}")
    if kospi_info:
        arrow = "🟢 상승" if kospi_info["above_ma20"] else "🔴 하락"
        print(f"   KOSPI: {kospi_info['price']} | MA20: {kospi_info['ma20']} ({kospi_info['deviation_pct']}%) | {arrow}")
    if usdkrw_info:
        arrow = "🔴 상승" if usdkrw_info["change_pct"] >= 0 else "🟢 하락"
        print(f"   USD/KRW: {usdkrw_info['current']} ({usdkrw_info['change_pct']:+.2f}%) | {arrow}")
    
    
    mode_us = determine_market_mode(macro, region="us")
    mode_kr = determine_market_mode(macro, region="kr")
    mode_map = {"normal": "🟢 일반", "adjust": "🟡 조정", "caution": "🟠 경계", "panic": "🔴 공포"}
    print(f"   미국 모드: {mode_map.get(mode_us, mode_us)} | 한국 모드: {mode_map.get(mode_kr, mode_kr)}")

    updated = datetime.now(KST).strftime("%m월 %d일 %H:%M")
    try:
        publish_payload_patch({
            "vix": macro.get("vix"),
            "qqq": macro.get("qqq"),
            "kospi": macro.get("kospi"),
            "usdkrw": macro.get("usdkrw"),
            "market_mode": mode_us,
            "market_mode_us": mode_us,
            "market_mode_kr": mode_kr,
            "updated": updated,
        })
    except Exception as e:
        print(f"   매크로 중간 업로드 에러: {e}")
    
    # === 국내 주식 ===
    print("\n🇰🇷 국내 주식...")
    try:
        token = get_kis_token()
    except Exception as e:
        print(f"   KIS 토큰 에러: {e}")
        token = None
    
    kr_stock_list = []
    for code, info in KR_STOCKS.items():
        name = info["name"]
        sector = info["sector"]
        print(f"   {name} ({sector})...")
        try:
            kis_data = get_kr_stock_data(token, code)
            fn_data = get_kr_valuation(code)
            time.sleep(0.5)
            
            merged = {**kis_data, **fn_data, "code": code, "name": name, "sector": sector}
            merged = merge_missing_from_snapshot(code, merged, KR_STOCK_SNAPSHOT_FIELDS)
            # target_gap 먼저 계산 (check_stock_signal에서 참조하기 위해)
            if merged.get("target_price") and merged.get("price"):
                merged["target_gap"] = round(
                    (merged["target_price"] - merged["price"]) / merged["price"] * 100, 1
                )
            
            # 스냅샷 저장
            save_snapshot(code, {
                **{field: merged.get(field) for field in KR_STOCK_SNAPSHOT_FIELDS},
                "data_as_of": merged.get("stale_as_of") or get_date_str(),
                "is_stale": merged.get("is_stale", False),
            })
            
            # 매수 시그널
            step1, step2, reason = check_stock_signal(merged, sector, macro, region="kr")
            merged["step1"] = step1
            merged["step2"] = step2
            if reason:
                merged["skip_reason"] = reason
            
            # 정보성 알림 (참고용)
            info_level, info_reasons = check_exit_signal(merged)
            merged["info_level"] = info_level
            merged["info_reasons"] = info_reasons
            merged["exit_level"] = info_level  # 하위 호환
            merged["exit_reasons"] = info_reasons
            
            # 3대 위기 트리거
            triggers, trigger_details = check_crisis_trigger(merged, macro)
            merged["crisis_triggers"] = triggers
            merged["crisis_details"] = trigger_details
            
            kr_stock_list.append(merged)
        except Exception as e:
            print(f"   에러: {e}")
            continue

    kr_stock_list = dedupe_records(kr_stock_list)
    try:
        publish_payload_patch({"kr_stock": kr_stock_list, "updated": updated})
        print(f"   국내 주식 중간 업로드: {len(kr_stock_list)}개")
    except Exception as e:
        print(f"   국내 주식 중간 업로드 에러: {e}")
    
    # === 국내 ETF ===
    print("\n🇰🇷 국내 ETF...")
    kr_etf_list = []
    for etf in dedupe_records(KR_ETFS, code_key="ticker_krx"):
        print(f"   {etf['name']}...")
        try:
            data = get_etf_data(etf)
            if not data:
                fallback = get_latest_valid_snapshot(etf["ticker_krx"], required_fields=("price",))
                if not fallback:
                    continue
                data = {field: fallback.get(field) for field in KR_ETF_SNAPSHOT_FIELDS}
                data["is_stale"] = True
                data["stale_as_of"] = fallback.get("data_as_of") or fallback.get("date")
                data["stale_filled_fields"] = [field for field in KR_ETF_SNAPSHOT_FIELDS if data.get(field) is not None]
                data["name"] = etf["name"]
                data["code"] = etf["ticker_krx"]
            else:
                data = merge_missing_from_snapshot(etf["ticker_krx"], data, KR_ETF_SNAPSHOT_FIELDS)
            
            step1, step2, reason = check_etf_signal(data, macro)
            data["step1"] = step1
            data["step2"] = step2
            if reason:
                data["skip_reason"] = reason
            
            save_snapshot(etf["ticker_krx"], {
                **{field: data.get(field) for field in KR_ETF_SNAPSHOT_FIELDS},
                "data_as_of": data.get("stale_as_of") or get_date_str(),
                "is_stale": data.get("is_stale", False),
            })
            
            kr_etf_list.append(data)
        except Exception as e:
            print(f"   에러 ({etf['name']}): {e}")
            continue
    
    kr_etf_list = dedupe_records(kr_etf_list)

    # === 미국 주식 ===
    print("\n🇺🇸 미국 주식...")
    us_stock_list = []
    for ticker, info in US_STOCKS.items():
        name = info["name"]
        sector = info["sector"]
        print(f"   {name} ({sector})...")
        try:
            data = get_us_stock_data(ticker)
            data["code"] = ticker
            data["name"] = name
            data["sector"] = sector
            
            # (target_gap은 get_us_stock_data 안에서 이미 계산됨)
            
            # 스냅샷 저장
            save_snapshot(ticker, {
                "eps_fwd": data.get("eps_fwd"),
                "peg_fwd": data.get("peg_fwd"),
                "pbr": data.get("pbr"),
                "per_fwd": data.get("per_fwd"),
                "ps": data.get("ps"),
                "target_price": data.get("target_price"),
                "earnings_surprise_pct": data.get("earnings_surprise_pct"),
                "price": data.get("price"),
                "rsi": data.get("rsi"),
            })
            
            # 매수 시그널
            step1, step2, reason = check_stock_signal(data, sector, macro, region="us")
            data["step1"] = step1
            data["step2"] = step2
            if reason:
                data["skip_reason"] = reason
            
            # 정보성 알림
            info_level, info_reasons = check_exit_signal(data)
            data["info_level"] = info_level
            data["info_reasons"] = info_reasons
            data["exit_level"] = info_level
            data["exit_reasons"] = info_reasons
            
            # 3대 위기 트리거
            triggers, trigger_details = check_crisis_trigger(data, macro)
            data["crisis_triggers"] = triggers
            data["crisis_details"] = trigger_details
            
            us_stock_list.append(data)
        except Exception as e:
            print(f"   에러: {e}")
            continue
    
    us_stock_list = dedupe_records(us_stock_list)

    # === Firestore ===
    print("\n☁️ Firestore 업로드...")
    date_str = get_date_str()
    
    payload = {
        "kr_stock": kr_stock_list,
        "kr_etf": kr_etf_list,
        "us_stock": us_stock_list,
        "vix": macro.get("vix"),
        "qqq": macro.get("qqq"),
        "kospi": macro.get("kospi"),
        "usdkrw": macro.get("usdkrw"),
        "market_mode": mode_us,       # 하위호환 (기존 PWA)
        "market_mode_us": mode_us,
        "market_mode_kr": mode_kr,
        "updated": updated,
    }
    
    publish_payload_patch(payload)
    
    print(f"\n📊 Read 통계:")
    print(f"   호출 횟수: {_read_counter['calls']}")
    print(f"   캐시 적중: {_read_counter['cache_hits']}")
    print(f"   실제 read: {_read_counter['actual_reads']}")
    print(f"\n✅ 완료! ({updated})")
    print(f"   국내 주식: {len(kr_stock_list)}개")
    print(f"   국내 ETF: {len(kr_etf_list)}개")
    print(f"   미국 주식: {len(us_stock_list)}개")
    print(f"   스냅샷 저장: {date_str}")
    
    try:
        if os.getenv("SAYO_SKIP_NOTIFY") == "1":
            print("   알림 스킵: SAYO_SKIP_NOTIFY=1")
        else:
            from notifier import check_and_notify
            check_and_notify(macro.get("vix"), macro.get("qqq"), macro.get("kospi"))
    except Exception as e:
        print(f"   알림 에러: {e}")


if __name__ == "__main__":
    upload_data()
