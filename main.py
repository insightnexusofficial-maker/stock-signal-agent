import requests
from bs4 import BeautifulSoup
import yfinance as yf
import os
import time
from dotenv import load_dotenv

load_dotenv()

# === KIS API ===
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

def get_access_token():
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(url, headers={"content-type": "application/json"}, json=body)
    return res.json().get("access_token")

def get_kr_price(token, code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST01010100"}
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    return int(data["output"]["stck_prpr"]) if data["rt_cd"] == "0" else None

def get_kr_daily(token, code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST03010100"}
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": "20250411", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    return [int(d["stck_clpr"]) for d in reversed(data["output2"])] if data.get("rt_cd") == "0" else []

# === RSI 계산 ===
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

# === FnGuide ===
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
            except: pass
    
    if result.get("per_forward") and result.get("eps_growth") and result["eps_growth"] > 0:
        result["peg_forward"] = round(result["per_forward"] / result["eps_growth"], 2)
    return result

# === yfinance ===
def get_us_data(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    result = {}
    
    result["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
    result["per_forward"] = info.get("forwardPE")
    
    eg = info.get("earningsGrowth")
    if eg: result["eps_growth"] = round(eg * 100, 1)
    
    rg = info.get("revenueGrowth")
    if rg: result["rev_growth"] = round(rg * 100, 1)
    
    if result.get("per_forward") and result.get("eps_growth") and result["eps_growth"] > 0:
        result["peg_forward"] = round(result["per_forward"] / result["eps_growth"], 2)
    
    hist = stock.history(period="3mo")
    if not hist.empty:
        result["rsi"] = calculate_rsi(hist["Close"].tolist())
    
    return result

# === 종목 리스트 ===
kr_stocks = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "012450": "한화에어로",
    "047810": "한국항공우주",
    "064350": "현대로템",
    "035420": "LIG넥스원"
}

us_stocks = {
    "NVDA": "엔비디아",
    "AVGO": "브로드컴",
    "MU": "마이크론",
    "AMD": "AMD",
    "TSM": "TSMC",
    "INTC": "인텔",
    "LMT": "록히드마틴",
    "RTX": "RTX",
    "NOC": "노스롭그루먼",
    "ATI": "ATI"
}

# === 메인 ===
print("\n📊 주식 사여?! - 매수 시그널 대시보드")
print("=" * 95)

# 국장
print("\n🇰🇷 국장")
print("-" * 95)
print(f"{'종목명':<12} {'현재가':>12} {'RSI':>6} {'Fwd PEG':>8} {'매출성장':>8} {'STEP1':^10} {'STEP2':^10}")
print("-" * 95)

token = get_access_token()
if token:
    for code, name in kr_stocks.items():
        time.sleep(1)
        price = get_kr_price(token, code)
        time.sleep(1)
        prices = get_kr_daily(token, code)
        rsi = calculate_rsi(prices) if prices else None
        val = get_kr_valuation(code)
        
        peg = val.get("peg_forward")
        rev_g = val.get("rev_growth")
        
        price_str = f"{price:,}원" if price else "N/A"
        rsi_str = f"{rsi}" if rsi else "N/A"
        peg_str = f"{peg:.2f}" if peg else "N/A"
        rev_str = f"{rev_g:.1f}%" if rev_g else "N/A"
        
        step1 = "✅ 사도됨" if (peg and peg < 1.0) and (rev_g and rev_g >= 15) else "❌"
        step2 = "🔔 지금!" if rsi and rsi < 35 else ("⚠️ 근접" if rsi and rsi < 40 else "-")
        
        print(f"{name:<12} {price_str:>12} {rsi_str:>6} {peg_str:>8} {rev_str:>8} {step1:^10} {step2:^10}")
        time.sleep(0.3)

# 미장
print("\n🇺🇸 미장")
print("-" * 95)
print(f"{'종목명':<12} {'현재가':>12} {'RSI':>6} {'Fwd PEG':>8} {'매출성장':>8} {'STEP1':^10} {'STEP2':^10}")
print("-" * 95)

for ticker, name in us_stocks.items():
    try:
        data = get_us_data(ticker)
        
        price = data.get("price")
        rsi = data.get("rsi")
        peg = data.get("peg_forward")
        rev_g = data.get("rev_growth")
        
        price_str = f"${price:,.2f}" if price else "N/A"
        rsi_str = f"{rsi}" if rsi else "N/A"
        peg_str = f"{peg:.2f}" if peg else "N/A"
        rev_str = f"{rev_g:.1f}%" if rev_g else "N/A"
        
        step1 = "✅ 사도됨" if (peg and peg < 1.0) and (rev_g and rev_g >= 15) else "❌"
        step2 = "🔔 지금!" if rsi and rsi < 35 else ("⚠️ 근접" if rsi and rsi < 40 else "-")
        
        print(f"{name:<12} {price_str:>12} {rsi_str:>6} {peg_str:>8} {rev_str:>8} {step1:^10} {step2:^10}")
    except Exception as e:
        print(f"{name:<12} 에러: {e}")
    time.sleep(0.3)

print("=" * 95)
print("✅ STEP1: Forward PEG < 1.0 AND Revenue Growth ≥ 15%")
print("🔔 STEP2: RSI(14) < 35 → 지금 사세요!")