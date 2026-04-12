import requests
from bs4 import BeautifulSoup
import os
import time
from dotenv import load_dotenv

load_dotenv()

# === KIS API 설정 ===
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

def get_access_token():
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, headers={"content-type": "application/json"}, json=body)
    return res.json().get("access_token")

def get_current_price(token, code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    if data["rt_cd"] == "0":
        return int(data["output"]["stck_prpr"])
    return None

def get_daily_prices(token, code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": "20250101",
        "FID_INPUT_DATE_2": "20250411",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    if data.get("rt_cd") == "0":
        return [int(d["stck_clpr"]) for d in reversed(data["output2"])]
    return []

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
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

# === FnGuide 스크래핑 ===
def get_valuation(code):
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}"
    res = session.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "lxml")
    session.close()
    
    tables = soup.select("table")
    result = {}
    
    if len(tables) < 12:
        return result
    
    table11 = tables[11]
    for row in table11.select("tr"):
        cells = [c.get_text(strip=True) for c in row.select("th, td")]
        
        if cells and cells[0] == "매출액":
            try:
                result["rev_2026"] = float(cells[6].replace(",", ""))
                result["rev_2027"] = float(cells[7].replace(",", ""))
                result["rev_growth"] = round((result["rev_2027"] - result["rev_2026"]) / result["rev_2026"] * 100, 1)
            except:
                pass
        
        if cells and "EPS" in cells[0] and "지배주주" in cells[0]:
            try:
                result["eps_2026"] = float(cells[6].replace(",", ""))
                result["eps_2027"] = float(cells[7].replace(",", ""))
                result["eps_growth"] = round((result["eps_2027"] - result["eps_2026"]) / result["eps_2026"] * 100, 1)
            except:
                pass
        
        if cells and "PER" in cells[0] and "수정주가" in cells[0]:
            try:
                result["per_forward"] = float(cells[6].replace(",", ""))
            except:
                pass
    
    if result.get("per_forward") and result.get("eps_growth") and result["eps_growth"] > 0:
        result["peg_forward"] = round(result["per_forward"] / result["eps_growth"], 2)
    
    return result

# === 메인 실행 ===
stocks = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "012450": "한화에어로",
    "047810": "한국항공우주",
    "064350": "현대로템",
    "035420": "LIG넥스원"
}

print("\n📊 토큰 발급 중...")
token = get_access_token()
if not token:
    print("토큰 발급 실패!")
    exit()

print("\n" + "=" * 90)
print(f"{'종목명':<10} {'현재가':>10} {'RSI':>6} {'Fwd PEG':>8} {'매출성장':>8} {'STEP1':^8} {'STEP2':^10}")
print("=" * 90)

for code, name in stocks.items():
    # KIS API
    time.sleep(1)
    price = get_current_price(token, code)
    time.sleep(1)
    prices = get_daily_prices(token, code)
    rsi = calculate_rsi(prices) if prices else None
    
    # FnGuide
    val = get_valuation(code)
    peg = val.get("peg_forward")
    rev_g = val.get("rev_growth")
    
    # 출력 포맷
    price_str = f"{price:,}원" if price else "N/A"
    rsi_str = f"{rsi}" if rsi else "N/A"
    peg_str = f"{peg:.2f}" if peg else "N/A"
    rev_str = f"{rev_g:.1f}%" if rev_g else "N/A"
    
    # STEP 1 판정: PEG < 1.0 AND Rev Growth >= 15%
    step1_pass = (peg and peg < 1.0) and (rev_g and rev_g >= 15)
    step1 = "✅ 사도됨" if step1_pass else "❌"
    
    # STEP 2 판정: RSI < 35
    if rsi and rsi < 35:
        step2 = "🔔 지금사세요"
    elif rsi and rsi < 40:
        step2 = "⚠️ 근접"
    else:
        step2 = "-"
    
    print(f"{name:<10} {price_str:>10} {rsi_str:>6} {peg_str:>8} {rev_str:>8} {step1:^8} {step2:^10}")
    
    time.sleep(0.5)

print("=" * 90)
print("STEP1: Forward PEG < 1.0 AND Revenue Growth ≥ 15%")
print("STEP2: RSI(14) < 35 → 🔔 지금사세요")