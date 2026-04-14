import firebase_admin
from firebase_admin import credentials, firestore
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import re
import os
import time
from datetime import datetime, timezone, timedelta
from config import KR_STOCKS, US_STOCKS, KR_ETFS
try:
    from config import SECTOR_CRITERIA
except ImportError:
    from config import SIGNAL_CONDITIONS

    # 구버전 config 호환: SIGNAL_CONDITIONS만 있을 때 최소 기준 구성
    SECTOR_CRITERIA = {
        "semiconductor": {
            "peg_threshold": SIGNAL_CONDITIONS["stock"]["peg_threshold"],
            "pbr_threshold": 3.0,
            "rsi_normal": SIGNAL_CONDITIONS["stock"]["rsi_threshold"],
            "rsi_caution": 30,
        },
        "ai_bigtech": {
            "peg_threshold": SIGNAL_CONDITIONS["stock"]["peg_threshold"],
            "rev_growth_threshold": SIGNAL_CONDITIONS["stock"]["rev_growth_threshold"],
            "consensus_gap_threshold": -10,
            "rsi_normal": SIGNAL_CONDITIONS["stock"]["rsi_threshold"],
            "rsi_caution": 30,
        },
        "defense": {
            "peg_threshold": 1.5,
            "per_threshold": 15,
            "rev_growth_threshold": 5,
            "rsi_normal": SIGNAL_CONDITIONS["stock"]["rsi_threshold"],
            "rsi_caution": 30,
        },
        "aerospace": {
            "peg_threshold": 1.5,
            "ps_threshold": 10,
            "band_threshold": 30,
            "rsi_normal": SIGNAL_CONDITIONS["stock"]["rsi_threshold"],
            "rsi_caution": 30,
        },
        "etf": {
            "rsi_normal": SIGNAL_CONDITIONS["etf"]["rsi_threshold"],
            "rsi_caution": 25,
            "nav_discount_threshold": SIGNAL_CONDITIONS["etf"]["nav_discount_threshold"],
            "band_threshold": 25,
        },
    }

# 구버전 config 호환: 문자열 종목명을 {"name","sector"} 형식으로 승격
if KR_STOCKS and isinstance(next(iter(KR_STOCKS.values())), str):
    KR_STOCKS = {code: {"name": name, "sector": "semiconductor"} for code, name in KR_STOCKS.items()}

if US_STOCKS and isinstance(next(iter(US_STOCKS.values())), str):
    US_STOCKS = {ticker: {"name": name, "sector": "ai_bigtech"} for ticker, name in US_STOCKS.items()}
from runtime_secrets import load_runtime_env, get_firebase_key_path

load_runtime_env()

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate(get_firebase_key_path())
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

def update_last_run(status, message=None, updated=None):
    payload = {
        "status": status,
        "checked_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    if message:
        payload["message"] = str(message)
    if updated:
        payload["updated"] = updated
    db.collection("state").document("last_run").set(payload, merge=True)

# ============================================================
# 매크로 지표 (VIX, QQQ)
# ============================================================
def get_macro_data():
    result = {"vix": None, "qqq": None}
    
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
                "reversal": reversal
            }
    except Exception as e:
        print(f"   VIX 에러: {e}")
    
    # QQQ MA20
    try:
        qqq = yf.Ticker("QQQ")
        hist = qqq.history(period="2mo")
        if len(hist) >= 20:
            current_price = float(hist["Close"].iloc[-1])
            ma20 = float(hist["Close"].iloc[-20:].mean())
            result["qqq"] = {
                "price": round(current_price, 2),
                "ma20": round(ma20, 2),
                "above_ma20": current_price > ma20
            }
    except Exception as e:
        print(f"   QQQ 에러: {e}")
    
    return result

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
    snapshots = get_snapshot_history(code, 35)
    
    if len(snapshots) < 5:
        return None, None, None
    
    snapshots.sort(key=lambda x: x["date"], reverse=True)
    
    recent_5 = [s.get("eps_fwd") for s in snapshots[:5] if s.get("eps_fwd")]
    v_curr = sum(recent_5) / len(recent_5) if recent_5 else None
    
    wow_data = [s.get("eps_fwd") for s in snapshots[7:12] if s.get("eps_fwd")]
    v_wow = sum(wow_data) / len(wow_data) if wow_data else None
    
    mom_data = [s.get("eps_fwd") for s in snapshots[28:33] if s.get("eps_fwd")]
    v_mom = sum(mom_data) / len(mom_data) if mom_data else None
    
    return v_curr, v_wow, v_mom

# ============================================================
# 국내 주식 (KIS API + FnGuide)
# ============================================================
def get_kis_token():
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    try:
        res = requests.post(url, headers={"content-type": "application/json"}, json=body, timeout=15)
        res.raise_for_status()
        token = res.json().get("access_token")
        if not token:
            print("   KIS 토큰 응답에 access_token 없음")
        return token
    except Exception as e:
        print(f"   KIS 토큰 발급 실패: {e}")
        return None

def get_kr_stock_data(token, code):
    result = {}
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST01010100"}
    
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        payload = res.json()
        if payload.get("rt_cd") == "0":
            output = payload["output"]
            result["price"] = int(output["stck_prpr"])
            result["volume"] = int(output.get("acml_vol", 0))
    except Exception as e:
        print(f"   현재가 조회 실패({code}): {e}")
    
    time.sleep(0.5)
    
    headers["tr_id"] = "FHKST03010100"
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    today = datetime.now(KST).strftime("%Y%m%d")
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": today, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        payload = res.json()
        if payload.get("rt_cd") == "0":
            candles = payload["output2"]
            prices = [int(d["stck_clpr"]) for d in reversed(candles)]
            result["rsi"] = calculate_rsi(prices)
            
            if len(candles) >= 20:
                vol_20 = [int(d.get("acml_vol", 0)) for d in candles[:20]]
                result["vol_avg_20"] = sum(vol_20) / 20
                if result["vol_avg_20"] > 0:
                    result["vol_ratio"] = round(result.get("volume", 0) / result["vol_avg_20"], 2)
    except Exception as e:
        print(f"   일봉 조회 실패({code}): {e}")
    
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
    if result.get("per_ttm") and result.get("eps_growth") and 5 <= result["eps_growth"] <= 200:
        result["peg_ttm"] = round(result["per_ttm"] / result["eps_growth"], 2)
    
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
# 미국 주식 (yfinance)
# ============================================================
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

    if result.get("per_ttm") and result.get("eps_growth"):
        peg_ttm = result["per_ttm"] / result["eps_growth"]
        if 0.05 <= peg_ttm <= 10:
            result["peg_ttm"] = round(peg_ttm, 2)
    
    target = info.get("targetMeanPrice")
    if target and result["price"]:
        result["consensus_gap"] = round((target - result["price"]) / result["price"] * 100, 1)
        result["target_price"] = target
    
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
# 섹터별 시그널 판정
# ============================================================
def check_stock_signal(data, sector, macro):
    # VIX 체크
    vix_mode = macro.get("vix", {}).get("mode", "normal")
    if vix_mode in ["level1", "level2"]:
        return False, False, f"VIX {vix_mode}"
    
    # QQQ 체크 → RSI 기준 결정
    qqq_above = macro.get("qqq", {}).get("above_ma20", True)
    criteria = SECTOR_CRITERIA.get(sector, {})
    
    if qqq_above:
        rsi_threshold = criteria.get("rsi_normal", 40)
        market_mode = "normal"
    else:
        rsi_threshold = criteria.get("rsi_caution", 30)
        market_mode = "caution"
    
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
    consensus_gap = data.get("consensus_gap")
    
    # EPS 추세
    v_curr, v_wow, v_mom = None, None, None
    if data.get("code"):
        v_curr, v_wow, v_mom = calculate_eps_trend(data["code"])
    
    eps_improving = True
    if v_curr and v_mom:
        eps_improving = v_curr > v_mom
    
    # 섹터별 Valuation 체크
    val_ok = False
    
    if sector == "semiconductor":
        peg_ok = peg is not None and peg < criteria["peg_threshold"]
        pbr_ok = pbr is not None and pbr < criteria["pbr_threshold"]
        val_ok = peg_ok and pbr_ok and eps_improving
        
    elif sector == "ai_bigtech":
        peg_ok = peg is not None and peg < criteria["peg_threshold"]
        rev_ok = rev_growth is not None and rev_growth >= criteria["rev_growth_threshold"]
        gap_ok = consensus_gap is None or consensus_gap > criteria.get("consensus_gap_threshold", -10)
        val_ok = peg_ok and rev_ok and gap_ok and eps_improving
        
    elif sector == "defense":
        peg_ok = peg is not None and peg < criteria["peg_threshold"]
        per_ok = per is not None and per < criteria["per_threshold"]
        rev_ok = rev_growth is not None and rev_growth >= criteria["rev_growth_threshold"]
        val_ok = (peg_ok or per_ok) and rev_ok
        
    elif sector == "aerospace":
        eps_fwd = data.get("eps_fwd", 0)
        if eps_fwd and eps_fwd > 0:
            val_ok = peg is not None and peg < criteria["peg_threshold"]
        else:
            ps_ok = ps is not None and ps < criteria["ps_threshold"]
            band_ok = band_pct is not None and band_pct < criteria["band_threshold"]
            val_ok = ps_ok and band_ok
    
    step1 = val_ok
    step2 = rsi is not None and rsi < rsi_threshold
    
    # 추가 정보
    data["market_mode"] = market_mode
    data["rsi_threshold"] = rsi_threshold
    
    return step1, step2, None

def check_etf_signal(data, macro):
    vix_mode = macro.get("vix", {}).get("mode", "normal")
    if vix_mode in ["level1", "level2"]:
        return False, False, f"VIX {vix_mode}"
    
    qqq_above = macro.get("qqq", {}).get("above_ma20", True)
    criteria = SECTOR_CRITERIA["etf"]
    
    if qqq_above:
        rsi_threshold = criteria["rsi_normal"]
        market_mode = "normal"
    else:
        rsi_threshold = criteria["rsi_caution"]
        market_mode = "caution"
    
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
# 메인 업로드 함수
# ============================================================
def upload_data():
    print("📊 데이터 수집 시작...\n")
    updated = datetime.now(KST).strftime("%m월 %d일 %H:%M")
    date_str = get_date_str()
    update_last_run("running", "스크리닝 시작", updated)

    try:
        # === 매크로 지표 ===
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
            print(f"   QQQ: ${qqq['price']} | MA20: ${qqq['ma20']} | {status}")
        
        # === 국내 주식 ===
        print("\n🇰🇷 국내 주식...")
        kr_stock_list = []
        token = get_kis_token()
        
        if token:
            for code, info in KR_STOCKS.items():
                name = info["name"]
                sector = info["sector"]
                print(f"   {name} ({sector})...")

                try:
                    stock_data = get_kr_stock_data(token, code)
                    val_data = get_kr_valuation(code)
                    
                    merged = {
                        "code": code,
                        "name": name,
                        "sector": sector,
                        **stock_data,
                        **val_data,
                    }
                    if merged.get("peg_fwd") is not None and merged.get("peg_forward") is None:
                        merged["peg_forward"] = merged.get("peg_fwd")
                    
                    if merged.get("target_price") and merged.get("price"):
                        merged["consensus_gap"] = round((merged["target_price"] - merged["price"]) / merged["price"] * 100, 1)
                    
                    save_snapshot(code, {
                        "eps_fwd": merged.get("eps_fwd"),
                        "peg_fwd": merged.get("peg_fwd"),
                        "peg_ttm": merged.get("peg_ttm"),
                        "pbr": merged.get("pbr"),
                        "price": merged.get("price"),
                        "rsi": merged.get("rsi"),
                    })
                    
                    step1, step2, reason = check_stock_signal(merged, sector, macro)
                    merged["step1"] = step1
                    merged["step2"] = step2
                    if reason:
                        merged["skip_reason"] = reason
                    
                    kr_stock_list.append(merged)
                except Exception as e:
                    print(f"   국내 종목 처리 실패({name}): {e}")
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
                if data.get("peg_fwd") is not None and data.get("peg_forward") is None:
                    data["peg_forward"] = data.get("peg_fwd")
                
                save_snapshot(ticker, {
                    "eps_fwd": data.get("eps_fwd"),
                    "peg_fwd": data.get("peg_fwd"),
                    "peg_ttm": data.get("peg_ttm"),
                    "pbr": data.get("pbr"),
                    "ps": data.get("ps"),
                    "price": data.get("price"),
                    "rsi": data.get("rsi"),
                })
                
                step1, step2, reason = check_stock_signal(data, sector, macro)
                data["step1"] = step1
                data["step2"] = step2
                if reason:
                    data["skip_reason"] = reason
                
                us_stock_list.append(data)
            except Exception as e:
                print(f"   에러: {e}")
            time.sleep(0.5)
        
        # === Firestore 업로드 ===
        print("\n☁️ Firestore 업로드...")
        db.collection("stocks").document("data").set({
            "kr_stock": kr_stock_list,
            "kr_etf": kr_etf_list,
            "us_stock": us_stock_list,
            "vix": macro.get("vix"),
            "qqq": macro.get("qqq"),
            "updated": updated
        })
        
        print(f"\n✅ 완료! ({updated})")
        print(f"   국내 주식: {len(kr_stock_list)}개")
        print(f"   국내 ETF: {len(kr_etf_list)}개")
        print(f"   미국 주식: {len(us_stock_list)}개")
        print(f"   스냅샷 저장: {date_str}")
        
        try:
            from notifier import check_and_notify
            check_and_notify(macro.get("vix"), macro.get("qqq"))
        except Exception as e:
            # 알림 실패가 데이터 업로드 자체를 실패로 만들지 않게 분리
            print(f"⚠️ 알림 처리 실패: {e}")

        update_last_run("success", "스크리닝 완료", updated)
    except Exception as e:
        update_last_run("failed", e, updated)
        raise

if __name__ == "__main__":
    upload_data()