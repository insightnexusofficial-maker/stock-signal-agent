import schedule
import time
import json
import os
import subprocess
from datetime import datetime
import pytz
import requests
from bs4 import BeautifulSoup
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# === 설정 ===
KST = pytz.timezone("Asia/Seoul")
EST = pytz.timezone("America/New_York")
STATE_FILE = "rsi_state.json"

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

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

# === 상태 저장/로드 ===
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# === 맥 알림 ===
def send_notification(title, message):
    script = f'display notification "{message}" with title "{title}" sound name "Glass"'
    subprocess.run(["osascript", "-e", script])
    print(f"   🔔 알림: {title} - {message}")

# === 장 시간 체크 ===
def is_kr_market_open():
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=0) <= now <= now.replace(hour=15, minute=30)

def is_us_market_open():
    now = datetime.now(EST)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=30) <= now <= now.replace(hour=16, minute=0)

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

# === KIS API ===
def get_kis_token():
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(url, headers={"content-type": "application/json"}, json=body)
    return res.json().get("access_token")

def get_kr_rsi(token, code):
    time.sleep(1)
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST03010100"}
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": "20250411", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    if data.get("rt_cd") == "0":
        prices = [int(d["stck_clpr"]) for d in reversed(data["output2"])]
        return calculate_rsi(prices)
    return None

# === yfinance ===
def get_us_rsi(ticker):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="3mo")
    if not hist.empty:
        return calculate_rsi(hist["Close"].tolist())
    return None

# === 시그널 체크 ===
def check_signals():
    now = datetime.now(KST)
    print(f"\n⏰ [{now.strftime('%Y-%m-%d %H:%M:%S')}] 시그널 체크 중...")
    
    kr_open = is_kr_market_open()
    us_open = is_us_market_open()
    
    print(f"   국장: {'🟢 장중' if kr_open else '🔴 장외'}")
    print(f"   미장: {'🟢 장중' if us_open else '🔴 장외'}")
    
    state = load_state()
    alerts = []
    
    # 국장 체크
    if kr_open:
        token = get_kis_token()
        if token:
            for code, name in KR_STOCKS.items():
                rsi = get_kr_rsi(token, code)
                prev_rsi = state.get(code, {}).get("rsi")
                
                if rsi:
                    # RSI 35 상향 돌파 감지
                    if prev_rsi and prev_rsi < 35 and rsi >= 35:
                        alerts.append(f"🇰🇷 {name} RSI {prev_rsi}→{rsi} 돌파!")
                    
                    state[code] = {"rsi": rsi, "name": name}
                    print(f"   {name}: RSI {rsi}")
                time.sleep(0.5)
    
    # 미장 체크
    if us_open:
        for ticker, name in US_STOCKS.items():
            rsi = get_us_rsi(ticker)
            prev_rsi = state.get(ticker, {}).get("rsi")
            
            if rsi:
                # RSI 35 상향 돌파 감지
                if prev_rsi and prev_rsi < 35 and rsi >= 35:
                    alerts.append(f"🇺🇸 {name} RSI {prev_rsi}→{rsi} 돌파!")
                
                state[ticker] = {"rsi": rsi, "name": name}
                print(f"   {name}: RSI {rsi}")
            time.sleep(0.3)
    
    # 상태 저장
    save_state(state)
    
    # 알림 발송
    for alert in alerts:
        send_notification("🔔 주식 사여?!", alert)
    
    if not alerts and (kr_open or us_open):
        print("   → 신규 시그널 없음")

# === 테스트 모드 ===
def test_notification():
    """알림 테스트"""
    send_notification("🔔 주식 사여?!", "테스트 알림입니다!")

# === 메인 ===
if __name__ == "__main__":
    print("📊 주식 사여?! - 스케줄러")
    print("=" * 50)
    
    # 알림 테스트
    test_notification()
    
    # 즉시 1회 실행 (장외라도 테스트용)
    # check_signals()
    
    # 10분마다 실행
    schedule.every(10).minutes.do(check_signals)
    
    print("\n⏳ 10분마다 체크합니다. (Ctrl+C로 종료)")
    
    while True:
        schedule.run_pending()
        time.sleep(1)