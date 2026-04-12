from firebase_functions import https_fn, scheduler_fn
from firebase_admin import initialize_app, firestore
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import time
import json

initialize_app()

# === 종목 리스트 ===
KR_STOCKS = {
    "005930": "삼성전자", "000660": "SK하이닉스", "012450": "한화에어로",
    "047810": "한국항공우주", "064350": "현대로템", "035420": "LIG넥스원"
}
US_STOCKS = {
    "NVDA": "엔비디아", "AVGO": "브로드컴", "MU": "마이크론", "AMD": "AMD",
    "TSM": "TSMC", "INTC": "인텔", "LMT": "록히드마틴", "RTX": "RTX",
    "NOC": "노스롭그루먼", "ATI": "ATI"
}

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

# === FnGuide 스크래핑 ===
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

# === API 엔드포인트 ===
@https_fn.on_request(cors=True)
def api_stocks(req: https_fn.Request) -> https_fn.Response:
    data = {"kr": [], "us": [], "updated": time.strftime("%m월 %d일 %H:%M")}
    
    # 미장 데이터 (국장은 KIS API 키 필요해서 일단 미장만)
    for ticker, name in US_STOCKS.items():
        try:
            stock_data = get_us_data(ticker)
            
            peg = stock_data.get("peg_forward")
            rev_g = stock_data.get("rev_growth")
            rsi = stock_data.get("rsi")
            
            step1 = (peg and peg < 1.0) and (rev_g and rev_g >= 15)
            step2 = rsi and rsi < 35
            
            data["us"].append({
                "code": ticker, "name": name,
                "price": stock_data.get("price"),
                "rsi": rsi, "peg": peg, "rev_growth": rev_g,
                "step1": step1, "step2": step2
            })
        except Exception as e:
            print(f"Error {ticker}: {e}")
        time.sleep(0.3)
    
    # 국장 데이터 (Firestore에서 읽기 - 별도 스케줄러가 업데이트)
    try:
        db = firestore.client()
        kr_doc = db.collection("stocks").document("kr_data").get()
        if kr_doc.exists:
            data["kr"] = kr_doc.to_dict().get("stocks", [])
    except:
        pass
    
    return https_fn.Response(
        json.dumps(data, ensure_ascii=False),
        content_type="application/json"
    )

# === Firestore에서 데이터 읽기 ===
@https_fn.on_request(cors=True)
def get_data(req: https_fn.Request) -> https_fn.Response:
    db = firestore.client()
    
    data = {"kr": [], "us": [], "updated": ""}
    
    # 국장
    kr_doc = db.collection("stocks").document("kr_data").get()
    if kr_doc.exists:
        kr_data = kr_doc.to_dict()
        data["kr"] = kr_data.get("stocks", [])
        data["updated"] = kr_data.get("updated", "")
    
    # 미장
    us_doc = db.collection("stocks").document("us_data").get()
    if us_doc.exists:
        us_data = us_doc.to_dict()
        data["us"] = us_data.get("stocks", [])
        if not data["updated"]:
            data["updated"] = us_data.get("updated", "")
    
    return https_fn.Response(
        json.dumps(data, ensure_ascii=False),
        content_type="application/json"
    )