import firebase_admin
from firebase_admin import credentials, firestore
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import os
import time
from dotenv import load_dotenv

load_dotenv()

# Firebase 초기화
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# KIS API 설정
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

KR_STOCKS = {
    "005930": "삼성전자", "000660": "SK하이닉스", "012450": "한화에어로",
    "047810": "한국항공우주", "064350": "현대로템", "035420": "LIG넥스원"
}
US_STOCKS = {
    "NVDA": "엔비디아", "AVGO": "브로드컴", "MU": "마이크론", "AMD": "AMD",
    "TSM": "TSMC", "INTC": "인텔", "LMT": "록히드마틴", "RTX": "RTX",
    "NOC": "노스롭그루먼", "ATI": "ATI"
}

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

def get_kis_token():
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(url, headers={"content-type": "application/json"}, json=body)
    return res.json().get("access_token")

def get_kr_data(token, code):
    result = {}
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST01010100"}
    
    # 현재가
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    res = requests.get(url, headers=headers, params=params)
    if res.json()["rt_cd"] == "0":
        result["price"] = int(res.json()["output"]["stck_prpr"])
    
    time.sleep(1)
    
    # RSI
    headers["tr_id"] = "FHKST03010100"
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": "20250412", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
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
            except: pass
    
    if result.get("per_forward") and result.get("eps_growth") and result["eps_growth"] > 0:
        result["peg_forward"] = round(result["per_forward"] / result["eps_growth"], 2)
    return result

def get_us_data(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    result = {}
    
    result["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
    
    eg = info.get("earningsGrowth")
    if eg: result["eps_growth"] = round(eg * 100, 1)
    
    rg = info.get("revenueGrowth")
    if rg: result["rev_growth"] = round(rg * 100, 1)
    
    if info.get("forwardPE") and result.get("eps_growth") and result["eps_growth"] > 0:
        result["peg_forward"] = round(info.get("forwardPE") / result["eps_growth"], 2)
    
    hist = stock.history(period="3mo")
    if not hist.empty:
        result["rsi"] = calculate_rsi(hist["Close"].tolist())
    
    return result

def upload_data():
    print("📊 데이터 수집 시작...\n")
    updated = time.strftime("%m월 %d일 %H:%M")
    
    # 국장
    print("🇰🇷 국장 데이터 수집...")
    kr_list = []
    token = get_kis_token()
    if token:
        for code, name in KR_STOCKS.items():
            print(f"   {name}...")
            stock_data = get_kr_data(token, code)
            val_data = get_kr_valuation(code)
            
            peg = val_data.get("peg_forward")
            rev_g = val_data.get("rev_growth")
            rsi = stock_data.get("rsi")
            
            step1 = (peg is not None and peg < 1.0) and (rev_g is not None and rev_g >= 15)
            step2 = rsi is not None and rsi < 35
            
            kr_list.append({
                "code": code, "name": name,
                "price": stock_data.get("price"),
                "rsi": rsi, "peg": peg, "rev_growth": rev_g,
                "step1": step1, "step2": step2
            })
            time.sleep(1)
    
    # 미장
    print("\n🇺🇸 미장 데이터 수집...")
    us_list = []
    for ticker, name in US_STOCKS.items():
        print(f"   {name}...")
        try:
            stock_data = get_us_data(ticker)
            
            peg = stock_data.get("peg_forward")
            rev_g = stock_data.get("rev_growth")
            rsi = stock_data.get("rsi")
            
            step1 = (peg is not None and peg < 1.0) and (rev_g is not None and rev_g >= 15)
            step2 = rsi is not None and rsi < 35
            
            us_list.append({
                "code": ticker, "name": name,
                "price": stock_data.get("price"),
                "rsi": rsi, "peg": peg, "rev_growth": rev_g,
                "step1": step1, "step2": step2
            })
        except Exception as e:
            print(f"   에러: {e}")
        time.sleep(0.5)
    
    # Firestore 업로드
    print("\n☁️ Firestore 업로드...")
    db.collection("stocks").document("data").set({
        "kr": kr_list,
        "us": us_list,
        "updated": updated
    })
    
    print(f"\n✅ 완료! ({updated})")
    print(f"   국장: {len(kr_list)}개")
    print(f"   미장: {len(us_list)}개")
    
    # 알림 체크
    from notifier import check_and_notify
    check_and_notify()

if __name__ == "__main__":
    upload_data()