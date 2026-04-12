import requests
import os
import time
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

# 1. 토큰 발급
def get_access_token():
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, headers=headers, json=body)
    return res.json().get("access_token")

# 2. 현재가 조회
def get_current_price(token, stock_code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code
    }
    res = requests.get(url, headers=headers, params=params)
    return res.json()

# 3. 일봉 데이터 조회
def get_daily_price(token, stock_code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": "20250101",
        "FID_INPUT_DATE_2": "20250411",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }
    res = requests.get(url, headers=headers, params=params)
    return res.json() if res.text else None

# 4. RSI 계산 (14일 기준)
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

# 실행
if __name__ == "__main__":
    print("1. 토큰 발급...")
    token = get_access_token()
    
    if not token:
        print("   토큰 발급 실패! 1분 후 다시 시도하세요.")
        exit()
    
    print(f"   토큰: {token[:20]}...\n")
    
    # 국장 종목 리스트
    stocks = {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "012450": "한화에어로스페이스",
        "047810": "한국항공우주",
        "064350": "현대로템",
        "035420": "LIG넥스원"
    }
    
    print("=" * 50)
    print(f"{'종목명':<15} {'현재가':>12} {'RSI(14)':>10}")
    print("=" * 50)
    
    for code, name in stocks.items():
        time.sleep(1)  # API 제한 방지
        
        # 현재가
        price_data = get_current_price(token, code)
        price = int(price_data['output']['stck_prpr']) if price_data["rt_cd"] == "0" else 0
        
        time.sleep(1)
        
        # RSI
        daily = get_daily_price(token, code)
        rsi = None
        if daily and daily.get("rt_cd") == "0":
            prices = [int(day['stck_clpr']) for day in reversed(daily['output2'])]
            rsi = calculate_rsi(prices)
        
        # 출력
        rsi_str = f"{rsi}" if rsi else "N/A"
        flag = "⚠️" if rsi and rsi < 35 else ""
        print(f"{name:<15} {price:>10,}원 {rsi_str:>10} {flag}")
    
    print("=" * 50)