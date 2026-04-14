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
from config import KR_STOCKS, US_STOCKS, KR_ETFS, SIGNAL_CONDITIONS

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

# ============================================================
# VIX 모니터링
# ============================================================
def get_vix_data():
    """VIX 지수 + 5일 이평 + 모드 판정"""
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="1mo")
        
        if hist.empty:
            return None
        
        current = round(float(hist["Close"].iloc[-1]), 2)
        ma5 = round(float(hist["Close"].iloc[-5:].mean()), 2) if len(hist) >= 5 else current
        prev = round(float(hist["Close"].iloc[-2]), 2) if len(hist) >= 2 else current
        
        # 모드 판정
        if current < 25:
            mode = "normal"
        elif current < 35:
            mode = "level1"
        else:
            mode = "level2"
        
        # 하락 반전 감지 (VIX가 MA5 아래로 내려오면)
        reversal = current < ma5 and prev >= ma5
        
        return {
            "current": current,
            "ma5": ma5,
            "prev": prev,
            "mode": mode,
            "reversal": reversal
        }
    except Exception as e:
        print(f"   VIX 에러: {e}")
        return None

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
        result["price"] = int(res.json()["output"]["stck_prpr"])
    
    time.sleep(1)
    
    headers["tr_id"] = "FHKST03010100"
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": "20250413", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
    res = requests.get(url, headers=headers, params=params)
    if res.json().get("rt_cd") == "0":
        prices = [int(d["stck_clpr"]) for d in reversed(res.json()["output2"])]
        result["rsi"] = calculate_rsi(prices)
    
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
    
    for row in tables[11].select("tr"):
        cells = [c.get_text(strip=True) for c in row.select("th, td")]
        if cells and cells[0] == "매출액":
            try:
                r26, r27 = float(cells[6].replace(",", "")), float(cells[7].replace(",", ""))
                result["rev_growth"] = round((r27 - r26) / r26 * 100, 1)
            except: pass
        if cells and "EPS" in cells[0] and "지배주주" in cells[0]:
            try:
                e26, e27 = float(cells[6].replace(",", "")), float(cells[7].replace(",", ""))
                result["eps_growth"] = round((e27 - e26) / e26 * 100, 1)
            except: pass
        if cells and "PER" in cells[0] and "수정주가" in cells[0]:
            try:
                result["per_forward"] = float(cells[6].replace(",", ""))
                result["per_ttm"] = float(cells[5].replace(",", ""))
            except: pass
    
    if result.get("per_forward") and result.get("eps_growth"):
        eg = result["eps_growth"]
        if 5 <= eg <= 200:
            result["peg_forward"] = round(result["per_forward"] / eg, 2)
    
    try:
        if result.get("per_ttm"):
            for row in tables[11].select("tr"):
                cells = [c.get_text(strip=True) for c in row.select("th, td")]
                if cells and "EPS" in cells[0] and "지배주주" in cells[0]:
                    e24 = float(cells[5].replace(",", ""))
                    e23 = float(cells[4].replace(",", ""))
                    if e23 > 0:
                        eps_growth_ttm = (e24 - e23) / e23 * 100
                        if 5 <= eps_growth_ttm <= 200:
                            result["peg_ttm"] = round(result["per_ttm"] / eps_growth_ttm, 2)
                    break
    except: pass
    
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
    result["per_forward"] = info.get("forwardPE")
    result["per_ttm"] = info.get("trailingPE")
    
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
        result["peg_forward"] = round(peg_yf, 2)
    elif result.get("per_forward") and result.get("eps_growth"):
        result["peg_forward"] = round(result["per_forward"] / result["eps_growth"], 2)
    
    peg_ttm_yf = info.get("trailingPegRatio")
    if peg_ttm_yf and 0.1 <= peg_ttm_yf <= 5:
        result["peg_ttm"] = round(peg_ttm_yf, 2)
    elif result.get("per_ttm") and result.get("eps_growth"):
        result["peg_ttm"] = round(result["per_ttm"] / result["eps_growth"], 2)
    
    target = info.get("targetMeanPrice")
    if target and result["price"]:
        result["consensus_gap"] = round((target - result["price"]) / result["price"] * 100, 1)
        result["target_price"] = target
    
    hist = stock.history(period="3mo")
    if not hist.empty:
        result["rsi"] = calculate_rsi(hist["Close"].tolist())
    
    return result

# ============================================================
# 국내 ETF (yfinance + 네이버 NAV)
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
        
        if len(hist) >= 2:
            prices_prev = hist["Close"].tolist()[:-1]
            result["rsi_prev"] = calculate_rsi(prices_prev)
        
        info = stock.info
        low52 = info.get("fiftyTwoWeekLow")
        high52 = info.get("fiftyTwoWeekHigh")
        if low52 and high52 and high52 > low52:
            result["band_pct"] = round((result["price"] - low52) / (high52 - low52) * 100, 1)
        
        if "Volume" in hist.columns and len(hist) >= 21:
            avg_vol = hist["Volume"].iloc[-21:-1].mean()
            latest_vol = hist["Volume"].iloc[-1]
            if avg_vol > 0:
                result["vol_ratio"] = round(float(latest_vol / avg_vol), 2)
        
    except Exception as e:
        print(f"   ETF 에러 ({etf['name']}): {e}")
        return None
    
    try:
        nav_data = fetch_nav_from_naver(etf["ticker_krx"])
        if nav_data and nav_data > 0:
            result["nav"] = nav_data
            result["nav_discount"] = round((result["price"] - nav_data) / nav_data * 100, 2)
    except: pass
    
    return result

def fetch_nav_from_naver(ticker_krx):
    try:
        url = f"https://finance.naver.com/item/sise.naver?code={ticker_krx}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res.encoding = "euc-kr"
        
        idx = res.text.find(">NAV<")
        if idx < 0:
            return None
        
        chunk = res.text[idx:idx + 2000]
        tds = re.findall(r"<td[^>]*>(.*?)</td>", chunk, re.DOTALL)
        td_values = [re.sub(r"<[^>]+>", "", td).strip() for td in tds if re.sub(r"<[^>]+>", "", td).strip()]
        
        if td_values:
            return float(td_values[0].replace(",", ""))
    except: pass
    return None

# ============================================================
# 시그널 판정
# ============================================================
def check_stock_signal(data):
    cond = SIGNAL_CONDITIONS["stock"]
    peg = data.get("peg_forward")
    rev_g = data.get("rev_growth")
    
    step1 = (peg is not None and peg < cond["peg_threshold"]) and (rev_g is not None and rev_g >= cond["rev_growth_threshold"])
    step2 = data.get("rsi") is not None and data.get("rsi") < cond["rsi_threshold"]
    
    return step1, step2

def check_etf_signal(data):
    cond = SIGNAL_CONDITIONS["etf"]
    rsi = data.get("rsi")
    nav_disc = data.get("nav_discount")
    vol_ratio = data.get("vol_ratio")
    
    step1 = rsi is not None and rsi <= cond["rsi_threshold"]
    step2 = (nav_disc is not None and nav_disc < cond["nav_discount_threshold"]) or (vol_ratio is not None and vol_ratio >= cond["vol_surge_threshold"])
    
    return step1, step2

# ============================================================
# 메인 업로드 함수
# ============================================================
def upload_data():
    print("📊 데이터 수집 시작...\n")
    KST = timezone(timedelta(hours=9))
    updated = datetime.now(KST).strftime("%m월 %d일 %H:%M")
    
    # === VIX 모니터링 ===
    print("📈 VIX 지수 확인...")
    vix_data = get_vix_data()
    if vix_data:
        print(f"   VIX: {vix_data['current']} | MA5: {vix_data['ma5']} | 모드: {vix_data['mode']}")
        if vix_data['reversal']:
            print("   ⚡ VIX 하락 반전 감지!")
    
    # === 국내 주식 ===
    print("\n🇰🇷 국내 주식...")
    kr_stock_list = []
    token = get_kis_token()
    if token:
        for code, name in KR_STOCKS.items():
            print(f"   {name}...")
            stock_data = get_kr_stock_data(token, code)
            val_data = get_kr_valuation(code)
            
            merged = {
                "code": code, "name": name,
                "price": stock_data.get("price"),
                "rsi": stock_data.get("rsi"),
                "peg_forward": val_data.get("peg_forward"),
                "peg_ttm": val_data.get("peg_ttm"),
                "rev_growth": val_data.get("rev_growth"),
                "target_price": val_data.get("target_price"),
            }
            
            if merged.get("target_price") and merged.get("price"):
                merged["consensus_gap"] = round((merged["target_price"] - merged["price"]) / merged["price"] * 100, 1)
            
            step1, step2 = check_stock_signal(merged)
            merged["step1"] = step1
            merged["step2"] = step2
            
            kr_stock_list.append(merged)
            time.sleep(1)
    
    # === 국내 ETF ===
    print("\n🇰🇷 국내 ETF...")
    kr_etf_list = []
    for etf in KR_ETFS:
        print(f"   {etf['name']}...")
        data = get_etf_data(etf)
        if data:
            step1, step2 = check_etf_signal(data)
            data["step1"] = step1
            data["step2"] = step2
            kr_etf_list.append(data)
        time.sleep(0.5)
    
    # === 미국 주식 ===
    print("\n🇺🇸 미국 주식...")
    us_stock_list = []
    for ticker, name in US_STOCKS.items():
        print(f"   {name}...")
        try:
            data = get_us_stock_data(ticker)
            data["code"] = ticker
            data["name"] = name
            
            step1, step2 = check_stock_signal(data)
            data["step1"] = step1
            data["step2"] = step2
            
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
        "vix": vix_data,
        "updated": updated
    })
    
    print(f"\n✅ 완료! ({updated})")
    print(f"   국내 주식: {len(kr_stock_list)}개")
    print(f"   국내 ETF: {len(kr_etf_list)}개")
    print(f"   미국 주식: {len(us_stock_list)}개")
    if vix_data:
        mode_emoji = {"normal": "🟢", "level1": "🟡", "level2": "🔴"}
        print(f"   VIX 모드: {mode_emoji.get(vix_data['mode'], '')} {vix_data['mode'].upper()}")
    
    # 알림 체크
    from notifier import check_and_notify
    check_and_notify(vix_data)

if __name__ == "__main__":
    upload_data()