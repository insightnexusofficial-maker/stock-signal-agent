"""
사여?! - 통합 설정 (v3: 섹터별 Earnings Surprise 공통화)
==================================================
국내주식 + 국내ETF + 미국주식
섹터별 매수/매도 임계값을 SECTOR_CRITERIA에서 일괄 관리
"""

# ============================================================
# 국내 주식 (KIS API + FnGuide)
# ============================================================
KR_STOCKS = {
    "005930": {"name": "삼성전자",   "sector": "semiconductor"},
    "000660": {"name": "SK하이닉스", "sector": "semiconductor"},
    "012450": {"name": "한화에어로", "sector": "defense"},
    "047810": {"name": "한국항공우주","sector": "aerospace"},
    "064350": {"name": "현대로템",   "sector": "defense"},
    "035420": {"name": "LIG넥스원",  "sector": "defense"},
}

# ============================================================
# 미국 주식 (yfinance)
# ============================================================
US_STOCKS = {
    "NVDA": {"name": "엔비디아",       "sector": "ai_bigtech"},
    "AVGO": {"name": "브로드컴",       "sector": "ai_bigtech"},
    "TSM":  {"name": "TSMC",           "sector": "ai_bigtech"},
    "MU":   {"name": "마이크론",       "sector": "semiconductor"},
    "AMD":  {"name": "AMD",            "sector": "semiconductor"},
    "INTC": {"name": "인텔",           "sector": "semiconductor"},
    "LMT":  {"name": "록히드마틴",     "sector": "defense"},
    "RTX":  {"name": "RTX",            "sector": "defense"},
    "NOC":  {"name": "노스롭그루먼",   "sector": "aerospace"},
    "ATI":  {"name": "ATI",            "sector": "aerospace"},
}

# ============================================================
# 국내 ETF (yfinance + 네이버 NAV)
# ============================================================
KR_ETFS = [
    {"name": "KODEX 미국S&P500",              "ticker_krx": "379800", "ticker_yf": "379800.KS"},
    {"name": "KODEX 200",                      "ticker_krx": "069500", "ticker_yf": "069500.KS"},
    {"name": "KODEX 반도체",                   "ticker_krx": "091160", "ticker_yf": "091160.KS"},
    {"name": "KODEX 미국우주항공",             "ticker_krx": "0167Z0", "ticker_yf": "0167Z0.KS"},
    {"name": "KODEX 미국AI테크TOP10",          "ticker_krx": "485540", "ticker_yf": "485540.KS"},
    {"name": "KODEX 미국AI테크TOP10타겟커버드콜","ticker_krx": "483280", "ticker_yf": "483280.KS"},
    {"name": "KODEX 200타겟위클리커버드콜",    "ticker_krx": "498400", "ticker_yf": "498400.KS"},
    {"name": "TIGER 반도체TOP10",              "ticker_krx": "396500", "ticker_yf": "396500.KS"},
    {"name": "TIGER 미국배당다우존스",         "ticker_krx": "458730", "ticker_yf": "458730.KS"},
]

# ============================================================
# 섹터별 임계값 (v3: consensus_gap_min 모든 섹터 추가)
# ============================================================
# 필드 설명:
#   peg_max               : PEG fwd 상한 (이하일 때 매수 후보)
#   valuation_metric      : 5년 밸류 위치 지표 ("pbr_5y_pct"/"per_5y_pct"/"evebitda_5y_pct")
#                           → P1에서 히스토리 데이터 연동 후 실제 작동
#   valuation_threshold   : 밸류 위치 상단 임계값 (이 이상이면 "가속도 점검" 구간)
#   pbr_max, per_max      : fallback 또는 OR 조건용 단순 상한
#   rev_growth_min        : 매출 성장률 하한 (%)
#   consensus_gap_min     : Earnings Surprise (%) 하한 — 실제 EPS vs 직전 분기 컨센
#                           0 = "플러스 서프라이즈만 통과", -10 = "10% 쇼크까진 허용"
#   ps_max, band_max      : 적자기업 대안 지표 (우주항공)
#   rsi_*                 : 시장 모드별 RSI 돌파 임계값 (아래→위 상향 돌파 기준)
# ============================================================
SECTOR_CRITERIA = {
    "semiconductor": {
        "peg_max": 0.5,
        "valuation_metric": "pbr_5y_pct",
        "valuation_threshold": 0.8,
        "pbr_max": 3.0,
        "consensus_gap_min": 0,      # 플러스 서프라이즈만
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "ai_bigtech": {
        "peg_max": 1.2,
        "valuation_metric": "per_5y_pct",   # 빅테크는 PBR 부적합 → PER 5년 위치
        "valuation_threshold": 0.8,
        "rev_growth_min": 15,
        "consensus_gap_min": -10,    # 빅테크는 변동성 커서 -10%까지 허용
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "defense": {
        "peg_max": 1.5,
        "valuation_metric": "pbr_5y_pct",
        "valuation_threshold": 0.8,
        "per_max": 15,
        "rev_growth_min": 5,
        "consensus_gap_min": 0,      # 플러스 서프라이즈만
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "aerospace": {
        "peg_max": 1.5,
        "valuation_metric": "pbr_5y_pct",
        "valuation_threshold": 0.8,
        "ps_max": 10,
        "band_max": 30,
        "consensus_gap_min": 0,      # 플러스 서프라이즈만
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "etf": {
        "rsi_normal": 30,
        "rsi_adjust": 28,
        "rsi_caution": 25,
        "rsi_panic": 20,
        "nav_discount_threshold": -0.5,
        "band_threshold": 25,
    },
}

# ============================================================
# EPS 추세 / Slope 계산 파라미터
# ============================================================
TREND_WINDOWS = {
    "curr_days": 5,    # V_curr: 최근 5거래일 평균
    "wow_offset": 7,   # V_wow: 7~11일 전 (5일 윈도우)
    "wow_days": 5,
    "mom_offset": 28,  # V_mom: 28~32일 전
    "mom_days": 5,
}

# 매도 단계화 (V_curr < V_wow 카운터)
EXIT_RULES = {
    "wow_warn_count": 1,   # 1회 하회 = 경고 알림
    "wow_sell_count": 2,   # 2회 연속 하회 = 절반 매도 알림
    "mom_sell": True,      # V_curr < V_mom = 즉시 전량 매도
    "raw_drop_pct": -3.0,  # 하루 raw 컨센 -3% 이상 급락 시 긴급 알림
}

# ============================================================
# 매크로 안전장치
# ============================================================
MACRO_GUARDS = {
    "qqq_ma_period": 20,        # QQQ 이동평균 기간
    "qqq_whipsaw_buffer": 0.01, # MA20 대비 -1% 이상 이탈해야 유효
    "qqq_confirm_days": 2,      # 2거래일 연속 하회 확인
    "kospi_ticker": "^KS11",    # KOSPI 종합 (또는 "069500.KS" KODEX200)
    "kospi_ma_period": 20,
}