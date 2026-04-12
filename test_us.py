import yfinance as yf
import time

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

def get_us_stock_data(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    
    result = {"ticker": ticker}
    
    # 현재가
    result["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
    
    # Forward PE
    result["per_forward"] = info.get("forwardPE")
    
    # Earnings Growth (%)
    eg = info.get("earningsGrowth")
    if eg:
        result["eps_growth"] = round(eg * 100, 1)
    
    # Revenue Growth (%)
    rg = info.get("revenueGrowth")
    if rg:
        result["rev_growth"] = round(rg * 100, 1)
    
    # Forward PEG 계산
    if result.get("per_forward") and result.get("eps_growth") and result["eps_growth"] > 0:
        result["peg_forward"] = round(result["per_forward"] / result["eps_growth"], 2)
    
    # Trailing PEG (백업용)
    result["peg_trailing"] = info.get("trailingPegRatio")
    
    # RSI 계산
    hist = stock.history(period="3mo")
    if not hist.empty:
        prices = hist["Close"].tolist()
        result["rsi"] = calculate_rsi(prices)
    
    return result


# 미장 10종목
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

print("\n" + "=" * 90)
print(f"{'티커':<6} {'종목명':<10} {'현재가':>10} {'RSI':>6} {'Fwd PEG':>8} {'매출성장':>8} {'STEP1':^10} {'STEP2':^8}")
print("=" * 90)

for ticker, name in us_stocks.items():
    try:
        data = get_us_stock_data(ticker)
        
        price = data.get("price")
        rsi = data.get("rsi")
        peg = data.get("peg_forward")
        rev_g = data.get("rev_growth")
        
        price_str = f"${price:,.2f}" if price else "N/A"
        rsi_str = f"{rsi}" if rsi else "N/A"
        peg_str = f"{peg:.2f}" if peg else "N/A"
        rev_str = f"{rev_g:.1f}%" if rev_g else "N/A"
        
        # STEP 1 판정: PEG < 1.0 AND Rev Growth >= 15%
        step1_pass = (peg and peg < 1.0) and (rev_g and rev_g >= 15)
        step1 = "✅ 사도됨" if step1_pass else "❌"
        
        # STEP 2 판정: RSI
        if rsi and rsi < 35:
            step2 = "🔔 지금!"
        elif rsi and rsi < 40:
            step2 = "⚠️ 근접"
        else:
            step2 = "-"
        
        print(f"{ticker:<6} {name:<10} {price_str:>10} {rsi_str:>6} {peg_str:>8} {rev_str:>8} {step1:^10} {step2:^8}")
        
    except Exception as e:
        print(f"{ticker:<6} {name:<10} 에러: {e}")
    
    time.sleep(0.3)

print("=" * 90)
print("STEP1: Forward PEG < 1.0 AND Revenue Growth ≥ 15%")
print("STEP2: RSI(14) < 35 → 🔔 지금!")