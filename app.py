from flask import Flask, jsonify, render_template_string
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import os
import time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# === 설정 ===
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

# === 함수들 ===
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
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST01010100"}
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    res = requests.get(url, headers=headers, params=params)
    if res.json()["rt_cd"] == "0":
        result["price"] = int(res.json()["output"]["stck_prpr"])
    
    time.sleep(0.5)
    
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers["tr_id"] = "FHKST03010100"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": "20250101", "FID_INPUT_DATE_2": "20250411", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
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

# === API ===
@app.route("/api/stocks")
def get_stocks():
    data = {"kr": [], "us": [], "updated": time.strftime("%m월 %d일 %H:%M")}
    
    token = get_kis_token()
    if token:
        for code, name in KR_STOCKS.items():
            stock_data = get_kr_data(token, code)
            val_data = get_kr_valuation(code)
            
            peg = val_data.get("peg_forward")
            rev_g = val_data.get("rev_growth")
            rsi = stock_data.get("rsi")
            
            step1 = (peg and peg < 1.0) and (rev_g and rev_g >= 15)
            step2 = rsi and rsi < 35
            
            data["kr"].append({
                "code": code, "name": name,
                "price": stock_data.get("price"),
                "rsi": rsi, "peg": peg, "rev_growth": rev_g,
                "step1": step1, "step2": step2
            })
            time.sleep(0.5)
    
    for ticker, name in US_STOCKS.items():
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
        time.sleep(0.3)
    
    return jsonify(data)

# === 웹 UI ===
@app.route("/")
def index():
    return render_template_string('''
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <title>주식 사여?!</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(180deg, #e8f4fc 0%, #d0e8f8 100%);
            min-height: 100vh;
            padding-bottom: 80px;
        }
        
        /* 상단 티커 바 */
        .ticker-bar {
            background: #f8fafc;
            padding: 8px 0;
            overflow-x: auto;
            white-space: nowrap;
            border-bottom: 1px solid #e2e8f0;
            -webkit-overflow-scrolling: touch;
        }
        .ticker-bar::-webkit-scrollbar { display: none; }
        .ticker-item {
            display: inline-block;
            padding: 4px 12px;
            font-size: 13px;
            color: #64748b;
        }
        .ticker-item span { font-weight: 600; color: #334155; }
        
        /* 컨테이너 */
        .container { max-width: 480px; margin: 0 auto; padding: 20px 16px; }
        
        /* 헤더 */
        .header {
            text-align: center;
            padding: 30px 0;
        }
        .header-icon {
            width: 100px;
            height: 100px;
            background: #fff;
            border-radius: 50%;
            margin: 0 auto 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 50px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .header h1 {
            font-size: 32px;
            color: #1e293b;
            margin-bottom: 8px;
        }
        .header p {
            color: #64748b;
            font-size: 15px;
        }
        
        /* 알림 카드 */
        .card {
            background: #fff;
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 16px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        }
        .card-title {
            font-size: 15px;
            font-weight: 600;
            color: #334155;
            margin-bottom: 12px;
        }
        
        /* 시그널 조건 */
        .signal-box {
            background: #f8fafc;
            border-radius: 12px;
            padding: 16px;
            margin-top: 12px;
        }
        .signal-item {
            font-size: 14px;
            color: #475569;
            margin-bottom: 8px;
        }
        .signal-item:last-child { margin-bottom: 0; }
        .signal-label {
            color: #3b82f6;
            font-weight: 600;
        }
        .signal-or {
            text-align: center;
            color: #94a3b8;
            font-size: 13px;
            margin: 12px 0;
        }
        
        /* 섹션 헤더 */
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin: 24px 0 12px;
        }
        .section-title {
            font-size: 18px;
            font-weight: 700;
            color: #1e293b;
        }
        .section-time {
            font-size: 13px;
            color: #94a3b8;
        }
        
        /* 탭 */
        .tabs {
            display: flex;
            background: #fff;
            border-radius: 12px;
            padding: 4px;
            margin-bottom: 16px;
        }
        .tab {
            flex: 1;
            padding: 10px;
            text-align: center;
            font-size: 14px;
            font-weight: 500;
            color: #64748b;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .tab.active {
            background: #3b82f6;
            color: #fff;
        }
        
        /* 종목 카드 */
        .stock-card {
            background: #fff;
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        }
        .stock-card.buy { border-left: 4px solid #22c55e; }
        .stock-card.alert { border-left: 4px solid #ef4444; }
        
        .stock-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }
        .stock-name {
            font-size: 16px;
            font-weight: 600;
            color: #1e293b;
        }
        .stock-price {
            font-size: 16px;
            font-weight: 700;
            color: #1e293b;
        }
        
        /* 지표 그리드 */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 8px;
        }
        .metric {
            background: #f8fafc;
            border-radius: 8px;
            padding: 10px 8px;
            text-align: center;
        }
        .metric-label {
            font-size: 11px;
            color: #94a3b8;
            margin-bottom: 4px;
        }
        .metric-value {
            font-size: 14px;
            font-weight: 600;
            color: #334155;
        }
        .metric-value.good { color: #22c55e; }
        .metric-value.warn { color: #f59e0b; }
        .metric-value.bad { color: #ef4444; }
        
        /* 배지 */
        .badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 8px;
        }
        .badge.green { background: #dcfce7; color: #16a34a; }
        .badge.red { background: #fee2e2; color: #dc2626; }
        .badge.yellow { background: #fef3c7; color: #d97706; }
        
        /* 하단 네비게이션 */
        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: #fff;
            display: flex;
            border-top: 1px solid #e2e8f0;
            padding: 8px 0 20px;
        }
        .nav-item {
            flex: 1;
            text-align: center;
            padding: 8px;
            font-size: 13px;
            color: #94a3b8;
            cursor: pointer;
            border-top: 2px solid transparent;
            transition: all 0.2s;
        }
        .nav-item.active {
            color: #3b82f6;
            border-top-color: #3b82f6;
        }
        
        /* 로딩 */
        .loading {
            text-align: center;
            padding: 60px 20px;
            color: #64748b;
        }
        .loading-spinner {
            width: 40px;
            height: 40px;
            border: 3px solid #e2e8f0;
            border-top-color: #3b82f6;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 16px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        
        /* 페이지 */
        .page { display: none; }
        .page.active { display: block; }
        
        /* 가이드 */
        .guide-section { margin-bottom: 20px; }
        .guide-title {
            font-size: 16px;
            font-weight: 600;
            color: #1e293b;
            margin-bottom: 8px;
        }
        .guide-desc {
            font-size: 14px;
            color: #64748b;
            margin-bottom: 12px;
        }
        .guide-box {
            background: #f8fafc;
            border-radius: 12px;
            padding: 16px;
        }
        .guide-item {
            font-size: 14px;
            color: #475569;
            margin-bottom: 6px;
        }
        .guide-item:last-child { margin-bottom: 0; }
        .guide-highlight { font-weight: 600; }
        .guide-highlight.blue { color: #3b82f6; }
        .guide-highlight.red { color: #ef4444; }
        .guide-highlight.green { color: #22c55e; }
    </style>
</head>
<body>
    <!-- 상단 티커 바 -->
    <div class="ticker-bar" id="tickerBar"></div>
    
    <!-- 페이지 1: 알림 설정 -->
    <div class="page active" id="page1">
        <div class="container">
            <div class="header">
                <div class="header-icon">📈</div>
                <h1>주식 사여?!</h1>
                <p>개미의 매수 타이밍 알림</p>
            </div>
            
            <div class="card">
                <div style="text-align: center; padding: 20px 0;">
                    <div style="display: inline-block; background: #f1f5f9; padding: 8px 16px; border-radius: 20px; color: #64748b; font-size: 14px;">OFF</div>
                    <p style="margin-top: 12px; font-size: 15px; color: #334155; font-weight: 500;">알림이 꺼져 있어요</p>
                    <p style="margin-top: 4px; font-size: 13px; color: #94a3b8;">켜두면 매수 타이밍에 알려드려요</p>
                </div>
                
                <div class="signal-box">
                    <div class="card-title">시그널 발생 조건</div>
                    <div class="signal-item">
                        <span class="signal-label">① Valuation:</span> Forward PEG &lt; 1.0 + 매출성장 ≥ 15%
                    </div>
                    <div class="signal-or">AND</div>
                    <div class="signal-item">
                        <span class="signal-label">② Timing:</span> RSI 35 하회 후 상향 돌파
                    </div>
                </div>
            </div>
            
            <button style="width: 100%; background: #3b82f6; color: #fff; border: none; padding: 16px; border-radius: 12px; font-size: 16px; font-weight: 600; cursor: pointer;">
                알림 켜기
            </button>
        </div>
    </div>
    
    <!-- 페이지 2: 주요 지표 -->
    <div class="page" id="page2">
        <div class="container">
            <div class="section-header">
                <div class="section-title">주요 지표</div>
                <div class="section-time" id="updateTime">-</div>
            </div>
            
            <div class="tabs">
                <div class="tab active" onclick="showMarket('kr')">🇰🇷 국장</div>
                <div class="tab" onclick="showMarket('us')">🇺🇸 미장</div>
            </div>
            
            <div id="stockList">
                <div class="loading">
                    <div class="loading-spinner"></div>
                    <div>데이터 로딩 중...</div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- 페이지 3: 가이드 -->
    <div class="page" id="page3">
        <div class="container">
            <div class="section-header">
                <div class="section-title">지표 가이드</div>
            </div>
            
            <div class="card">
                <div class="guide-section">
                    <div class="guide-title">RSI</div>
                    <div class="guide-desc">14일간 가격 흐름을 0~100으로 표현</div>
                    <div class="guide-box">
                        <div class="guide-item"><span class="guide-highlight blue">35 이하</span> — 과매도, 매수 기회 가능성</div>
                        <div class="guide-item"><span class="guide-highlight red">70 이상</span> — 과매수, 조정 가능성</div>
                        <div class="guide-item"><span class="guide-highlight">40~60</span> — 중립 구간</div>
                    </div>
                </div>
                
                <div class="guide-section">
                    <div class="guide-title">Forward PEG</div>
                    <div class="guide-desc">Forward PER ÷ EPS 성장률 (2026E→2027E)</div>
                    <div class="guide-box">
                        <div class="guide-item"><span class="guide-highlight green">1.0 미만</span> — 저평가, 매수 매력</div>
                        <div class="guide-item"><span class="guide-highlight">1.0~1.5</span> — 적정</div>
                        <div class="guide-item"><span class="guide-highlight red">1.5 초과</span> — 고평가</div>
                    </div>
                </div>
                
                <div class="guide-section">
                    <div class="guide-title">매출 성장률</div>
                    <div class="guide-desc">2026E → 2027E 예상 매출 증가율</div>
                    <div class="guide-box">
                        <div class="guide-item"><span class="guide-highlight green">15% 이상</span> — 고성장</div>
                        <div class="guide-item"><span class="guide-highlight">5~15%</span> — 안정 성장</div>
                        <div class="guide-item"><span class="guide-highlight red">5% 미만</span> — 저성장</div>
                    </div>
                </div>
                
                <div class="guide-section">
                    <div class="guide-title">시그널 조건</div>
                    <div class="guide-desc">두 조건 모두 충족 시 알림 발송</div>
                    <div class="guide-box">
                        <div class="guide-item"><span class="guide-highlight blue">① Valuation 매수</span><br>Forward PEG &lt; 1.0 + 매출성장 ≥ 15%</div>
                        <div style="text-align: center; color: #94a3b8; font-size: 13px; margin: 12px 0;">AND</div>
                        <div class="guide-item"><span class="guide-highlight blue">② Timing 매수</span><br>RSI 35 하회 후 상향 돌파</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- 하단 네비게이션 -->
    <div class="bottom-nav">
        <div class="nav-item active" onclick="showPage(1)">알림 설정</div>
        <div class="nav-item" onclick="showPage(2)">주요 지표</div>
        <div class="nav-item" onclick="showPage(3)">가이드</div>
    </div>
    
    <script>
        let stockData = { kr: [], us: [] };
        let currentMarket = 'kr';
        
        function showPage(num) {
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            document.getElementById('page' + num).classList.add('active');
            document.querySelectorAll('.nav-item')[num - 1].classList.add('active');
            
            if (num === 2 && stockData.kr.length === 0) {
                loadData();
            }
        }
        
        function showMarket(market) {
            currentMarket = market;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            renderStocks();
        }
        
        function renderStocks() {
            const list = stockData[currentMarket];
            if (list.length === 0) return;
            
            let html = '';
            list.forEach(s => {
                const cardClass = s.step2 ? 'alert' : (s.step1 ? 'buy' : '');
                const priceStr = currentMarket === 'kr' 
                    ? (s.price ? s.price.toLocaleString() + '원' : '-')
                    : (s.price ? '$' + s.price.toFixed(2) : '-');
                
                const rsiClass = s.rsi < 35 ? 'bad' : (s.rsi < 40 ? 'warn' : '');
                const pegClass = s.peg && s.peg < 1.0 ? 'good' : (s.peg && s.peg > 1.5 ? 'bad' : '');
                const revClass = s.rev_growth && s.rev_growth >= 15 ? 'good' : (s.rev_growth && s.rev_growth < 5 ? 'bad' : '');
                
                let badges = '';
                if (s.step1) badges += '<span class="badge green">사도됨</span>';
                if (s.step2) badges += '<span class="badge red">지금!</span>';
                else if (s.rsi && s.rsi < 40) badges += '<span class="badge yellow">RSI근접</span>';
                
                html += `
                    <div class="stock-card ${cardClass}">
                        <div class="stock-header">
                            <div class="stock-name">${s.name}${badges}</div>
                            <div class="stock-price">${priceStr}</div>
                        </div>
                        <div class="metrics-grid">
                            <div class="metric">
                                <div class="metric-label">RSI</div>
                                <div class="metric-value ${rsiClass}">${s.rsi || '-'}</div>
                            </div>
                            <div class="metric">
                                <div class="metric-label">PEG</div>
                                <div class="metric-value ${pegClass}">${s.peg || '-'}</div>
                            </div>
                            <div class="metric">
                                <div class="metric-label">매출성장</div>
                                <div class="metric-value ${revClass}">${s.rev_growth ? s.rev_growth + '%' : '-'}</div>
                            </div>
                            <div class="metric">
                                <div class="metric-label">상태</div>
                                <div class="metric-value ${s.step1 ? 'good' : ''}">${s.step1 ? 'BUY' : '-'}</div>
                            </div>
                        </div>
                    </div>
                `;
            });
            
            document.getElementById('stockList').innerHTML = html;
        }
        
        function updateTickerBar() {
            const all = [...stockData.kr, ...stockData.us];
            let html = '';
            all.forEach(s => {
                if (s.rsi) {
                    html += `<span class="ticker-item">${s.name} <span>${s.rsi}</span></span>`;
                }
            });
            document.getElementById('tickerBar').innerHTML = html;
        }
        
        function loadData() {
            document.getElementById('stockList').innerHTML = `
                <div class="loading">
                    <div class="loading-spinner"></div>
                    <div>데이터 로딩 중...</div>
                </div>
            `;
            
            fetch('/api/stocks')
                .then(res => res.json())
                .then(data => {
                    stockData = data;
                    document.getElementById('updateTime').textContent = data.updated;
                    renderStocks();
                    updateTickerBar();
                })
                .catch(err => {
                    document.getElementById('stockList').innerHTML = `
                        <div class="loading">에러: ${err}</div>
                    `;
                });
        }
    </script>
</body>
</html>
    ''')

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)