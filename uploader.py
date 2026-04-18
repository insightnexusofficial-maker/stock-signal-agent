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
    SECTOR_CRITERIA, TREND_WINDOWS, EXIT_RULES, MACRO_GUARDS,
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
# 공통 함수
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
    return round((curr / base - 1) * 100, 2)

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
    
    # Whipsaw 완충: 최근 confirm_days 동안 모두 (MA × (1-buffer)) 이하여야 "이탈 확정"
    if len(closes) < period + confirm_days - 1:
        # 데이터 부족 시 단순 판정
        return {
            "price": round(current_price, 2),
            "ma20": round(ma_current, 2),
            "above_ma20": current_price > ma_current,
            "deviation_pct": deviation_pct,
        }
    
    below_confirmed = True
    for i in range(confirm_days):
        idx_end = len(closes) - i          # exclusive
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
    
    # QQQ MA20 (whipsaw 완충)
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
    
    # KOSPI MA20 (한국 매크로)
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

# ============================================================
# 시장 모드 판정
# ============================================================
def determine_market_mode(macro, region="us"):
    """
    region="us": VIX + QQQ (미국주식 및 글로벌 ETF)
    region="kr": VIX + KOSPI (국내주식 및 국내 ETF)
    """
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
    doc_ref.set(data)

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

def calculate_eps_trend(code):
    """
    V_curr/V_wow/V_mom (5일 MA) + raw 일별 변화 병행.
    트리거 민감도 위해 raw도 함께 반환.
    """
    snapshots = get_snapshot_history(code, 35)
    
    if len(snapshots) < 2:
        return {}
    
    snapshots.sort(key=lambda x: x["date"], reverse=True)
    
    # Raw 일별 변화 (트리거용)
    raw_today = snapshots[0].get("eps_fwd") if snapshots else None
    raw_yesterday = snapshots[1].get("eps_fwd") if len(snapshots) >= 2 else None
    raw_change_pct = _slope_pct(raw_today, raw_yesterday)
    
    # V_curr / V_wow / V_mom (스무딩된 추세)
    cw = TREND_WINDOWS["curr_days"]
    wo = TREND_WINDOWS["wow_offset"]
    wd = TREND_WINDOWS["wow_days"]
    mo = TREND_WINDOWS["mom_offset"]
    md = TREND_WINDOWS["mom_days"]
    
    v_curr = _avg([s.get("eps_fwd") for s in snapshots[:cw]])
    v_wow = _avg([s.get("eps_fwd") for s in snapshots[wo:wo + wd]])
    v_mom = _avg([s.get("eps_fwd") for s in snapshots[mo:mo + md]])
    
    slope_wow_pct = _slope_pct(v_curr, v_wow)
    slope_mom_pct = _slope_pct(v_curr, v_mom)
    
    wow_violated = v_curr is not None and v_wow is not None and v_curr < v_wow
    mom_violated = v_curr is not None and v_mom is not None and v_curr < v_mom
    
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
    res = session.get(url, headers={"User-Agent": "Mozilla/5.0"})
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
    """최근 발표된 분기 Earnings Surprise (%). 실패 시 None."""
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
        # yfinance는 0.05 = 5% 형태로 주는 경우가 있어 보정
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
    
    # 최근 분기 Earnings Surprise (실제 vs 발표 당시 컨센)
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
# 섹터별 매수 시그널
# ============================================================
def check_stock_signal(data, sector, macro, region="us"):
    criteria = SECTOR_CRITERIA.get(sector, {})
    market_mode = determine_market_mode(macro, region=region)
    rsi_threshold = get_rsi_threshold(criteria, market_mode)
    
    data["market_mode"] = market_mode
    data["rsi_threshold"] = rsi_threshold
    
    # EPS 추세 (raw + smoothed 모두 data에 병합)
    trend = {}
    if data.get("code"):
        trend = calculate_eps_trend(data["code"])
    for k, v in trend.items():
        data[f"trend_{k}"] = v
    
    # 유동성 체크
    vol_ratio = data.get("vol_ratio", 0)
    if vol_ratio < 1.2:
        return False, False, "거래량 부족"
    
    rsi = data.get("rsi")
    peg = data.get("peg_fwd")
    pbr = data.get("pbr")
    per = data.get("per_fwd") or data.get("per_ttm")
    ps = data.get("ps")
    rev_growth = data.get("rev_growth")
    band_pct = data.get("band_pct")
    earnings_surprise = data.get("earnings_surprise_pct")
    
    # 이익 추세 개선: V_curr > V_mom (slope_mom_pct > 0)
    slope_mom = trend.get("slope_mom_pct")
    eps_improving = slope_mom is not None and slope_mom > 0
    
    # 공통 필터: Earnings Surprise (데이터 있을 때만 체크)
    surprise_threshold = criteria.get("consensus_gap_min", 0)
    surprise_ok = earnings_surprise is None or earnings_surprise >= surprise_threshold
    
    # 데이터 부족(slope None) 시 eps_improving은 통과 (초기 도입 유예)
    if slope_mom is None:
        eps_improving = True
    
    val_ok = False
    
    if sector == "semiconductor":
        peg_ok = peg is not None and peg < criteria.get("peg_max", 0.5)
        pbr_ok = pbr is not None and pbr < criteria.get("pbr_max", 3.0)
        val_ok = peg_ok and pbr_ok and eps_improving and surprise_ok
        
    elif sector == "ai_bigtech":
        peg_ok = peg is not None and peg < criteria.get("peg_max", 1.2)
        rev_ok = rev_growth is not None and rev_growth >= criteria.get("rev_growth_min", 15)
        val_ok = peg_ok and rev_ok and surprise_ok and eps_improving
        
    elif sector == "defense":
        peg_ok = peg is not None and peg < criteria.get("peg_max", 1.5)
        per_ok = per is not None and per < criteria.get("per_max", 15)
        rev_ok = rev_growth is not None and rev_growth >= criteria.get("rev_growth_min", 5)
        val_ok = (peg_ok or per_ok) and rev_ok and eps_improving and surprise_ok
        
    elif sector == "aerospace":
        eps_fwd = data.get("eps_fwd", 0)
        if eps_fwd and eps_fwd > 0:
            val_ok = peg is not None and peg < criteria.get("peg_max", 1.5)
        else:
            ps_ok = ps is not None and ps < criteria.get("ps_max", 10)
            band_ok = band_pct is not None and band_pct < criteria.get("band_max", 30)
            val_ok = ps_ok and band_ok
        val_ok = val_ok and eps_improving and surprise_ok
    
    step1 = val_ok
    step2 = rsi is not None and rsi < rsi_threshold
    
    return step1, step2, None

def check_etf_signal(data, macro):
    criteria = SECTOR_CRITERIA["etf"]
    # ETF는 국내 상장 기준이므로 KOSPI 매크로 사용
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
# 매도 시그널 (신규)
# ============================================================
def check_exit_signal(data):
    """
    매도 단계 판정.
    - sell_full: V_curr < V_mom (추세 파괴)
    - warn:      V_curr < V_wow (단기 모멘텀 약화, 2회 연속은 notifier에서 sell_half로 승격)
    - warn:      raw 일별 변화 <= -3% (컨센 급락)
    """
    reasons = []
    level = None
    
    slope_mom = data.get("trend_slope_mom_pct")
    slope_wow = data.get("trend_slope_wow_pct")
    wow_violated = data.get("trend_wow_violated", False)
    mom_violated = data.get("trend_mom_violated", False)
    raw_change = data.get("trend_raw_change_pct")
    
    if EXIT_RULES.get("mom_sell", True) and mom_violated:
        level = "sell_full"
        if slope_mom is not None:
            reasons.append(f"추세 파괴 (V_curr<V_mom, {slope_mom}%)")
        else:
            reasons.append("추세 파괴 (V_curr<V_mom)")
    
    raw_drop_limit = EXIT_RULES.get("raw_drop_pct", -3.0)
    if raw_change is not None and raw_change <= raw_drop_limit:
        if level != "sell_full":
            level = "warn"
        reasons.append(f"컨센 raw 급락 ({raw_change}%)")
    
    if wow_violated and level is None:
        level = "warn"
        if slope_wow is not None:
            reasons.append(f"단기 모멘텀 약화 (V_curr<V_wow, {slope_wow}%)")
        else:
            reasons.append("단기 모멘텀 약화 (V_curr<V_wow)")
    
    return level, reasons

# ============================================================
# 메인 업로드
# ============================================================
def upload_data():
    print("📊 데이터 수집 시작...\n")
    updated = datetime.now(KST).strftime("%m월 %d일 %H:%M")
    date_str = get_date_str()
    
    # === 매크로 ===
    print("📈 매크로 지표 확인...")
    macro = get_macro_data()
    
    if macro.get("vix"):
        vix = macro["vix"]
        mode_emoji = {"normal": "🟢", "level1": "🟡", "level2": "🔴"}
        print(f"   VIX: {vix['current']} | 모드: {mode_emoji[vix['mode']]} {vix['mode'].upper()}")
        if vix.get("reversal"):
            print("   ⚡ VIX 하락 반전 감지!")
    
    if macro.get("qqq"):
        qqq = macro["qqq"]
        status = "🟢 상승" if qqq["above_ma20"] else "🟡 경계"
        print(f"   QQQ: ${qqq['price']} | MA20: ${qqq['ma20']} ({qqq.get('deviation_pct')}%) | {status}")
    
    if macro.get("kospi"):
        ks = macro["kospi"]
        status = "🟢 상승" if ks["above_ma20"] else "🟡 경계"
        print(f"   KOSPI: {ks['price']} | MA20: {ks['ma20']} ({ks.get('deviation_pct')}%) | {status}")
    
    mode_us = determine_market_mode(macro, region="us")
    mode_kr = determine_market_mode(macro, region="kr")
    mode_label = {"normal": "🟢 일반", "adjust": "🟡 조정", "caution": "🟠 경계", "panic": "🔴 공포"}
    print(f"   미국 모드: {mode_label.get(mode_us)} | 한국 모드: {mode_label.get(mode_kr)}\n")
    
    # === 국내 주식 ===
    print("🇰🇷 국내 주식...")
    kr_stock_list = []
    token = get_kis_token()
    
    if token:
        for code, info in KR_STOCKS.items():
            name = info["name"]
            sector = info["sector"]
            print(f"   {name} ({sector})...")
            
            stock_data = get_kr_stock_data(token, code)
            val_data = get_kr_valuation(code)
            
            merged = {
                "code": code,
                "name": name,
                "sector": sector,
                **stock_data,
                **val_data,
            }
            
            if merged.get("target_price") and merged.get("price"):
                merged["target_gap"] = round((merged["target_price"] - merged["price"]) / merged["price"] * 100, 1)
            
            # 매일 raw 스냅샷 (기울기 계산용)
            save_snapshot(code, {
                "eps_fwd": merged.get("eps_fwd"),
                "peg_fwd": merged.get("peg_fwd"),
                "pbr": merged.get("pbr"),
                "per_fwd": merged.get("per_fwd"),
                "target_price": merged.get("target_price"),
                "price": merged.get("price"),
                "rsi": merged.get("rsi"),
            })
            
            step1, step2, reason = check_stock_signal(merged, sector, macro, region="kr")
            merged["step1"] = step1
            merged["step2"] = step2
            if reason:
                merged["skip_reason"] = reason
            
            exit_level, exit_reasons = check_exit_signal(merged)
            merged["exit_level"] = exit_level
            merged["exit_reasons"] = exit_reasons
            
            kr_stock_list.append(merged)
            time.sleep(1)
    
    # === 국내 ETF ===
    print("\n🇰🇷 국내 ETF...")
    kr_etf_list = []
    for etf in KR_ETFS:
        print(f"   {etf['name']}...")
        data = get_etf_data(etf)
        if data:
            step1, step2, reason = check_etf_signal(data, macro)
            data["step1"] = step1
            data["step2"] = step2
            if reason:
                data["skip_reason"] = reason
            kr_etf_list.append(data)
        time.sleep(0.5)
    
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
            
            step1, step2, reason = check_stock_signal(data, sector, macro, region="us")
            data["step1"] = step1
            data["step2"] = step2
            if reason:
                data["skip_reason"] = reason
            
            exit_level, exit_reasons = check_exit_signal(data)
            data["exit_level"] = exit_level
            data["exit_reasons"] = exit_reasons
            
            us_stock_list.append(data)
        except Exception as e:
            print(f"   에러: {e}")
        time.sleep(0.5)
    
    # === Firestore ===
    print("\n☁️ Firestore 업로드...")
    db.collection("stocks").document("data").set({
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
    })
    
    print(f"\n✅ 완료! ({updated})")
    print(f"   국내 주식: {len(kr_stock_list)}개")
    print(f"   국내 ETF: {len(kr_etf_list)}개")
    print(f"   미국 주식: {len(us_stock_list)}개")
    print(f"   스냅샷 저장: {date_str}")
    
    from notifier import check_and_notify
    check_and_notify(macro.get("vix"), macro.get("qqq"), macro.get("kospi"))

if __name__ == "__main__":
    upload_data()
