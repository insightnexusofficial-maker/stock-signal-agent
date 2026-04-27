import firebase_admin
from firebase_admin import credentials, firestore
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import re
import os
import time
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

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

KST = timezone(timedelta(hours=9))


# ============================================================
# 공통 유틸
# ============================================================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0)
        losses.append(abs(change) if change < 0 else 0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def get_date_str():
    return datetime.now(KST).strftime("%Y%m%d")


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

# ============================================================
# 매크로 지표 (VIX, QQQ, KOSPI)
# ============================================================
def _calc_ma_status(hist, period=20, buffer_pct=0.01, confirm_days=2):
    """MA 이탈 판정 (whipsaw 완충 포함)."""
    if hist is None or hist.empty or len(hist) < period:
        return None
    
    closes = hist["Close"]
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


def get_macro_data():
    result = {"vix": None, "qqq": None, "kospi": None}
    
    buf = MACRO_GUARDS.get("qqq_whipsaw_buffer", 0.01)
    confirm = MACRO_GUARDS.get("qqq_confirm_days", 2)
    
    # VIX
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="1mo")
        if not hist.empty:
            current = round(float(hist["Close"].iloc[-1]), 2)
            ma5 = round(float(hist["Close"].iloc[-5:].mean()), 2) if len(hist) >= 5 else current
            prev = round(float(hist["Close"].iloc[-2]), 2) if len(hist) >= 2 else current
            
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
    
    return result


def determine_market_mode(macro, region="us"):
    """region='us' → VIX+QQQ / region='kr' → VIX+KOSPI."""
    vix_data = macro.get("vix") or {}
    idx_data = macro.get("kospi") if region == "kr" else macro.get("qqq")
    idx_data = idx_data or {}
    
    vix_mode = vix_data.get("mode", "normal")
    idx_above = idx_data.get("above_ma20", True)
    
    if vix_mode == "level2":
        return "panic"
    elif vix_mode == "level1":
        return "caution"
    elif not idx_above:
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
    date_str = get_date_str()
    doc_ref = db.collection("snapshots").document(date_str).collection("stocks").document(code)
    doc_ref.set(_sanitize_for_firestore(data))

def get_snapshot_history(code, days=35):
    snapshots = []
    base_date = datetime.now(KST)
    
    for i in range(days):
        target_date = base_date - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        
        try:
            doc = db.collection("snapshots").document(date_str).collection("stocks").document(code).get()
            if doc.exists:
                data = doc.to_dict()
                data["date"] = date_str
                snapshots.append(data)
        except:
            pass
    
    return snapshots


# ============================================================
# 기울기 계산 (EPS + 목표주가 공통 일반화)
# ============================================================
def _calculate_trend_generic(code, field_name):
    snapshots = get_snapshot_history(code, 35)
    if len(snapshots) < 2:
        return {}
    
    snapshots.sort(key=lambda x: x["date"], reverse=True)
    
    raw_today = snapshots[0].get(field_name) if snapshots else None
    raw_yesterday = snapshots[1].get(field_name) if len(snapshots) >= 2 else None
    raw_change_pct = _slope_pct(raw_today, raw_yesterday)
    
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
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(url, headers={"content-type": "application/json"}, json=body)
    return res.json().get("access_token")


def get_kr_stock_data(token, code):
    result = {}
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST01010100"}
    
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    res = requests.get(url, headers=headers, params=params)
    if res.json()["rt_cd"] == "0":
        output = res.json()["output"]
        result["price"] = int(output["stck_prpr"])
        result["volume"] = int(output.get("acml_vol", 0))
    
    time.sleep(0.5)
    
    headers["tr_id"] = "FHKST03010100"
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    today = datetime.now(KST).strftime("%Y%m%d")
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": today, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
    res = requests.get(url, headers=headers, params=params)
    if res.json().get("rt_cd") == "0":
        candles = res.json()["output2"]
        prices = [int(d["stck_clpr"]) for d in reversed(candles)]
        result["rsi"] = calculate_rsi(prices)
        
        if len(candles) >= 20:
            vol_20 = [int(d.get("acml_vol", 0)) for d in candles[:20]]
            result["vol_avg_20"] = sum(vol_20) / 20
            if result["vol_avg_20"] > 0:
                result["vol_ratio"] = round(result["volume"] / result["vol_avg_20"], 2)
    
    return result


def get_kr_valuation(code):
    session = requests.Session()
    url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}"
    res = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    soup = BeautifulSoup(res.text, "lxml")
    session.close()
    
    result = {}
    tables = soup.select("table")
    if len(tables) < 12:
        return result
    
    try:
        snap_table = soup.select("div.corp_group1 table")
        if snap_table:
            for row in snap_table[0].select("tr"):
                cells = [c.get_text(strip=True) for c in row.select("th, td")]
                if cells and "PBR" in cells[0]:
                    result["pbr"] = float(cells[1].replace(",", ""))
                    break
    except: pass
    
    for row in tables[11].select("tr"):
        cells = [c.get_text(strip=True) for c in row.select("th, td")]
        if cells and cells[0] == "매출액":
            try:
                r26, r27 = float(cells[6].replace(",", "")), float(cells[7].replace(",", ""))
                result["rev_growth"] = round((r27 - r26) / r26 * 100, 1)
            except: pass
        if cells and "EPS" in cells[0] and "지배주주" in cells[0]:
            try:
                result["eps_fwd"] = float(cells[7].replace(",", ""))
                e26, e27 = float(cells[6].replace(",", "")), float(cells[7].replace(",", ""))
                result["eps_growth"] = round((e27 - e26) / e26 * 100, 1)
            except: pass
        if cells and "PER" in cells[0] and "수정주가" in cells[0]:
            try:
                result["per_fwd"] = float(cells[6].replace(",", ""))
                result["per_ttm"] = float(cells[5].replace(",", ""))
            except: pass
    
    if result.get("per_fwd") and result.get("eps_growth") and 5 <= result["eps_growth"] <= 200:
        result["peg_fwd"] = round(result["per_fwd"] / result["eps_growth"], 2)
    
    try:
        consensus_section = soup.select_one("div.corp_group2")
        if consensus_section:
            target_text = consensus_section.get_text()
            match = re.search(r'목표주가[^\d]*(\d[\d,]*)', target_text)
            if match:
                result["target_price"] = float(match.group(1).replace(",", ""))
    except: pass
    
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
    
    eg = info.get("earningsGrowth")
    if eg is not None:
        eps_growth_pct = eg * 100
        if 5 <= eps_growth_pct <= 200:
            result["eps_growth"] = round(eps_growth_pct, 1)
    
    rg = info.get("revenueGrowth")
    if rg is not None:
        result["rev_growth"] = round(rg * 100, 1)
    
    peg_yf = info.get("pegRatio")
    if peg_yf and 0.1 <= peg_yf <= 5:
        result["peg_fwd"] = round(peg_yf, 2)
    elif result.get("per_fwd") and result.get("eps_growth"):
        result["peg_fwd"] = round(result["per_fwd"] / result["eps_growth"], 2)
    
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
def get_etf_data(etf):
    ticker = etf["ticker_yf"]
    result = {"name": etf["name"], "code": etf["ticker_krx"]}
    
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="3mo")
        if hist.empty or len(hist) < 15:
            return None
        
        result["price"] = round(float(hist["Close"].iloc[-1]), 0)
        result["rsi"] = calculate_rsi(hist["Close"].tolist())
        result["volume"] = int(hist["Volume"].iloc[-1])
        
        if len(hist) >= 20:
            vol_avg = hist["Volume"].iloc[-20:].mean()
            if vol_avg > 0:
                result["vol_ratio"] = round(result["volume"] / vol_avg, 2)
        
        info = stock.info
        low52 = info.get("fiftyTwoWeekLow")
        high52 = info.get("fiftyTwoWeekHigh")
        if low52 and high52 and high52 > low52:
            result["band_pct"] = round((result["price"] - low52) / (high52 - low52) * 100, 1)
        
    except Exception as e:
        print(f"   ETF 에러 ({etf['name']}): {e}")
        return None
    
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
    pbr = data.get("pbr")
    per = data.get("per_fwd") or data.get("per_ttm")
    ps = data.get("ps")
    rev_growth = data.get("rev_growth")
    band_pct = data.get("band_pct")
    earnings_surprise = data.get("earnings_surprise_pct")
    target_gap = data.get("target_gap")
    
    # === EPS 기울기 필수 조건 (매우 관대: slope_mom ≥ -1%) ===
    slope_mom = eps_trend.get("slope_mom_pct")
    slope_required = SLOPE_RULES.get("required_mom_min", -1.0)
    # 데이터 부족 시 유예 (초기 도입 단계)
    eps_trend_ok = slope_mom is None or slope_mom >= slope_required
    
    # === 섹터별 필수 valuation 체크 ===
    val_ok = False
    
    if sector == "semiconductor":
        val_ok = peg is not None and peg < criteria.get("peg_max", 0.8)
    
    elif sector == "ai_bigtech":
        val_ok = peg is not None and peg < criteria.get("peg_max", 1.2)
    
    elif sector == "defense":
        peg_ok = peg is not None and peg < criteria.get("peg_max", 1.5)
        per_ok = per is not None and per < criteria.get("per_max", 15)
        val_ok = peg_ok or per_ok
    
    elif sector == "aerospace":
        eps_fwd = data.get("eps_fwd", 0) or 0
        if eps_fwd > 0:
            val_ok = peg is not None and peg < criteria.get("peg_max", 1.5)
        else:
            ps_ok = ps is not None and ps < criteria.get("ps_max", 10)
            band_ok = band_pct is not None and band_pct < criteria.get("band_max", 30)
            val_ok = ps_ok and band_ok
    
    elif sector == "industrial":
        # 산업재/중공업: 사이클 종목. PBR 제외, PER < 12 (저점 진입 기준)
        peg_ok = peg is not None and peg < criteria.get("peg_max", 1.2)
        per_ok = per is not None and per < criteria.get("per_max", 12)
        val_ok = peg_ok or per_ok
    
    elif sector == "growth":
        # 그로스주 (쿠팡/유니티/테슬라): 흑자면 PEG, 적자면 PS+밴드
        # 우주항공(aerospace)과 유사한 패턴
        eps_fwd = data.get("eps_fwd", 0) or 0
        if eps_fwd > 0:
            # 흑자: PEG 기준
            val_ok = peg is not None and peg < criteria.get("peg_max", 2.0)
        else:
            # 적자: PS + 52주 밴드 위치
            ps_ok = ps is not None and ps < criteria.get("ps_max", 8)
            band_ok = band_pct is not None and band_pct < criteria.get("band_max", 40)
            val_ok = ps_ok and band_ok
    
    # === 선택 조건 2/3 체크 ===
    hits = 0
    
    # 선택 1: 매출 성장률
    rev_min = criteria.get("rev_growth_min")
    if rev_min is not None and rev_growth is not None and rev_growth >= rev_min:
        hits += 1
    
    # 선택 2: Earnings Surprise
    surprise_min = criteria.get("consensus_gap_min")
    if surprise_min is not None and earnings_surprise is not None and earnings_surprise >= surprise_min:
        hits += 1
    
    # 선택 3: 목표주가 갭
    target_min = criteria.get("target_gap_min", 0)
    if target_gap is not None and target_gap >= target_min:
        hits += 1
    
    data["selection_hits"] = hits
    
    # === 유동성 체크 (hits 계산 후, step1 판정 전) ===
    vol_ratio = data.get("vol_ratio", 0) or 0
    vol_min = criteria.get("volume_min_ratio", 1.0)
    vol_ok = vol_ratio >= vol_min
    
    # === Step 1 통과 판정 ===
    step1 = val_ok and eps_trend_ok and hits >= 2 and vol_ok
    
    # === RSI 매수 구간 판정 ===
    zone_upper = rsi_threshold + BUY_LEVELS.get("candidate_rsi_upper_offset", 10)
    in_zone = rsi is not None and rsi_threshold <= rsi <= zone_upper
    data["in_buy_zone"] = in_zone
    
    # === 매수 레벨 판정 (candidate / strong) ===
    buy_level = "none"
    if step1 and in_zone:
        # 가점: EPS slope > +3% AND 목표주가 slope > +3%
        eps_bonus_min = SLOPE_RULES.get("bonus_mom_min", 3.0)
        eps_bonus = (slope_mom is not None and slope_mom > eps_bonus_min)
        target_slope_mom = target_trend.get("slope_mom_pct")
        target_bonus = (target_slope_mom is not None and target_slope_mom > eps_bonus_min)
        
        if eps_bonus and target_bonus:
            buy_level = "strong"  # 🟢🟢 강력 매수
        else:
            buy_level = "candidate"  # 🟢 매수 후보
    
    data["buy_level"] = buy_level
    
    # Step 2: 매수 구간 안이면 True (notifier.py에서 "돌파" 감지)
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
    
    rsi_ok = rsi is not None and rsi <= rsi_threshold
    nav_ok = nav_discount is not None and nav_discount < criteria["nav_discount_threshold"]
    band_ok = band_pct is not None and band_pct < criteria["band_threshold"]
    
    step1 = (rsi_ok or nav_ok) and band_ok
    step2 = rsi_ok
    
    data["market_mode"] = market_mode
    data["rsi_threshold"] = rsi_threshold
    
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
    
    if vix_info:
        print(f"   VIX: {vix_info['current']} | 모드: 🟢 {vix_info['mode'].upper()}")
    if qqq_info:
        arrow = "🟢 상승" if qqq_info["above_ma20"] else "🔴 하락"
        print(f"   QQQ: ${qqq_info['price']} | MA20: ${qqq_info['ma20']} ({qqq_info['deviation_pct']}%) | {arrow}")
    if kospi_info:
        arrow = "🟢 상승" if kospi_info["above_ma20"] else "🔴 하락"
        print(f"   KOSPI: {kospi_info['price']} | MA20: {kospi_info['ma20']} ({kospi_info['deviation_pct']}%) | {arrow}")
    
    mode_us = determine_market_mode(macro, region="us")
    mode_kr = determine_market_mode(macro, region="kr")
    mode_map = {"normal": "🟢 일반", "adjust": "🟡 조정", "caution": "🟠 경계", "panic": "🔴 공포"}
    print(f"   미국 모드: {mode_map.get(mode_us, mode_us)} | 한국 모드: {mode_map.get(mode_kr, mode_kr)}")
    
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
            if not token:
                continue
            
            kis_data = get_kr_stock_data(token, code)
            fn_data = get_kr_valuation(code)
            time.sleep(0.5)
            
            merged = {**kis_data, **fn_data, "code": code, "name": name, "sector": sector}
            
            # target_gap 먼저 계산 (check_stock_signal에서 참조하기 위해)
            if merged.get("target_price") and merged.get("price"):
                merged["target_gap"] = round(
                    (merged["target_price"] - merged["price"]) / merged["price"] * 100, 1
                )
            
            # 스냅샷 저장
            save_snapshot(code, {
                "eps_fwd": merged.get("eps_fwd"),
                "peg_fwd": merged.get("peg_fwd"),
                "pbr": merged.get("pbr"),
                "per_fwd": merged.get("per_fwd"),
                "target_price": merged.get("target_price"),
                "price": merged.get("price"),
                "rsi": merged.get("rsi"),
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
    
    # === 국내 ETF ===
    print("\n🇰🇷 국내 ETF...")
    kr_etf_list = []
    for etf in KR_ETFS:
        print(f"   {etf['name']}...")
        try:
            data = get_etf_data(etf)
            if not data:
                continue
            
            step1, step2, reason = check_etf_signal(data, macro)
            data["step1"] = step1
            data["step2"] = step2
            if reason:
                data["skip_reason"] = reason
            
            kr_etf_list.append(data)
        except Exception as e:
            print(f"   에러 ({etf['name']}): {e}")
            continue
    
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
    
    # === Firestore ===
    print("\n☁️ Firestore 업로드...")
    date_str = get_date_str()
    updated = datetime.now(KST).strftime("%m월 %d일 %H:%M")
    
    payload = {
        "kr_stock": kr_stock_list,
        "kr_etf": kr_etf_list,
        "us_stock": us_stock_list,
        "vix": macro.get("vix"),
        "qqq": macro.get("qqq"),
        "kospi": macro.get("kospi"),
        "market_mode": mode_us,       # 하위호환 (기존 PWA)
        "market_mode_us": mode_us,
        "market_mode_kr": mode_kr,
        "updated": updated,
    }
    
    # numpy 타입 정제 (Firestore는 numpy 타입 거부)
    payload = _sanitize_for_firestore(payload)    
    db.collection("stocks").document("data").set(payload)
    
    print(f"\n✅ 완료! ({updated})")
    print(f"   국내 주식: {len(kr_stock_list)}개")
    print(f"   국내 ETF: {len(kr_etf_list)}개")
    print(f"   미국 주식: {len(us_stock_list)}개")
    print(f"   스냅샷 저장: {date_str}")
    
    try:
        from notifier import check_and_notify
        check_and_notify(macro.get("vix"), macro.get("qqq"), macro.get("kospi"))
    except Exception as e:
        print(f"   알림 에러: {e}")


if __name__ == "__main__":
    upload_data()