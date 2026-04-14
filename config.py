# config.py

KR_STOCKS = {
    "005930": {"name": "삼성전자", "sector": "semiconductor"},
    "000660": {"name": "SK하이닉스", "sector": "semiconductor"},
    "012450": {"name": "한화에어로", "sector": "defense"},
    "047810": {"name": "한국항공우주", "sector": "aerospace"},
    "064350": {"name": "현대로템", "sector": "defense"},
    "079550": {"name": "LIG넥스원", "sector": "defense"},
}

US_STOCKS = {
    "NVDA": {"name": "엔비디아", "sector": "ai_bigtech"},
    "AVGO": {"name": "브로드컴", "sector": "ai_bigtech"},
    "MU": {"name": "마이크론", "sector": "semiconductor"},
    "AMD": {"name": "AMD", "sector": "semiconductor"},
    "TSM": {"name": "TSMC", "sector": "ai_bigtech"},
    "INTC": {"name": "인텔", "sector": "semiconductor"},
    "LMT": {"name": "록히드마틴", "sector": "defense"},
    "RTX": {"name": "RTX", "sector": "defense"},
    "NOC": {"name": "노스롭그루먼", "sector": "aerospace"},
    "ATI": {"name": "ATI", "sector": "aerospace"},
}

KR_ETFS = [
    {"name": "KODEX 미국S&P500", "ticker_krx": "379800", "ticker_yf": "379800.KS"},
    {"name": "KODEX 200", "ticker_krx": "069500", "ticker_yf": "069500.KS"},
    {"name": "KODEX 반도체", "ticker_krx": "091160", "ticker_yf": "091160.KS"},
    {"name": "KODEX 미국AI테크TOP10", "ticker_krx": "485540", "ticker_yf": "485540.KS"},
    {"name": "KODEX 미국AI테크TOP10타겟커버드콜", "ticker_krx": "483280", "ticker_yf": "483280.KS"},
    {"name": "KODEX 200타겟위클리커버드콜", "ticker_krx": "498400", "ticker_yf": "498400.KS"},
    {"name": "TIGER 반도체TOP10", "ticker_krx": "396500", "ticker_yf": "396500.KS"},
    {"name": "TIGER 미국배당다우존스", "ticker_krx": "458730", "ticker_yf": "458730.KS"},
]

# 섹터별 Valuation 기준
SECTOR_CRITERIA = {
    "semiconductor": {
        "peg_threshold": 0.5,
        "pbr_threshold": 3.0,
        "rsi_normal": 40,      # QQQ > MA20
        "rsi_caution": 30,     # QQQ < MA20
    },
    "ai_bigtech": {
        "peg_threshold": 1.2,
        "rev_growth_threshold": 15,
        "consensus_gap_threshold": -10,
        "rsi_normal": 40,
        "rsi_caution": 30,
    },
    "defense": {
        "peg_threshold": 1.5,
        "per_threshold": 15,
        "rev_growth_threshold": 5,
        "rsi_normal": 40,
        "rsi_caution": 30,
    },
    "aerospace": {
        "peg_threshold": 1.5,
        "ps_threshold": 10,
        "band_threshold": 30,
        "rsi_normal": 40,
        "rsi_caution": 30,
    },
    "etf": {
        "rsi_normal": 30,
        "rsi_caution": 25,
        "nav_discount_threshold": -0.5,
        "band_threshold": 25,
    },
}