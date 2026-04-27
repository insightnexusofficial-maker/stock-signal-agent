"""
사여?! - 통합 설정 (v4: PBR 재배치 + 기울기 민감도 + 매수 3단계)
==================================================
국내주식 + 국내ETF + 미국주식
섹터별 매수/매도 임계값을 SECTOR_CRITERIA에서 일괄 관리
"""

# ============================================================
# 국내 주식 (KIS API + FnGuide)
# ============================================================
KR_STOCKS = {
    "005930": {"name": "삼성전자",         "sector": "semiconductor"},
    "000660": {"name": "SK하이닉스",       "sector": "semiconductor"},
    "267270": {"name": "HD현대건설기계",   "sector": "industrial"},
    "034020": {"name": "두산에너빌리티",   "sector": "industrial"},
    "010120": {"name": "LS ELECTRIC",      "sector": "industrial"},
    "251270": {"name": "넷마블",           "sector": "growth"},
}   

# ============================================================
# 미국 주식 (yfinance)
# ============================================================
US_STOCKS = {
    "NVDA":  {"name": "엔비디아",       "sector": "ai_bigtech"},
    "AVGO":  {"name": "브로드컴",       "sector": "ai_bigtech"},
    "MU":    {"name": "마이크론",       "sector": "semiconductor"},
    "MSFT":  {"name": "마이크로소프트", "sector": "ai_bigtech"},
    "GOOGL": {"name": "구글",           "sector": "ai_bigtech"},
    "CPNG":  {"name": "쿠팡",           "sector": "growth"},
    "U":     {"name": "유니티 소프트웨어","sector": "growth"},
    "TSLA":  {"name": "테슬라",         "sector": "growth"},
}

# ============================================================
# 국내 ETF (yfinance + 네이버 NAV)
# ============================================================
KR_ETFS = [
    {"name": "Kodex 미국우주항공", "ticker_krx": "0167Z0", "ticker_yf": "0167Z0.KS"},
    {"name": "Kodex 200타겟위클리커버드콜", "ticker_krx": "456600", "ticker_yf": "456600.KS"},
    {"name": "Tiger 반도체", "ticker_krx": "091230", "ticker_yf": "091230.KS"},
    {"name": "Kodex 미국AI테크TOP10", "ticker_krx": "478150", "ticker_yf": "478150.KS"},
    {"name": "Kodex 방산Top10", "ticker_krx": "0080G0", "ticker_yf": "0080G0.KS"},
    {"name": "Kodex 미국AI광통신", "ticker_krx": "0173Y0", "ticker_yf": "0173Y0.KS"},
    {"name": "ACE 미국S&P500", "ticker_krx": "360200", "ticker_yf": "360200.KS"},
]

# ============================================================
# 섹터별 임계값 (v4: PBR 재배치 + 필수/선택 구조)
# ============================================================
# 필드 설명:
#   peg_max               : PEG fwd 상한 (필수)
#   pbr_max               : PBR 상한 (방산만 필수, 나머지 없음)
#   per_max               : OR 조건용 PER 상한 (방산)
#   rev_growth_min        : 매출 성장률 하한 (선택 조건)
#   consensus_gap_min     : Earnings Surprise 하한 (선택 조건)
#   ps_max, band_max      : 적자기업 대안 (우주항공)
#   volume_min_ratio      : 거래량 / 20일 평균 최소 배수 (완화됨 1.0)
#   rsi_*                 : 시장 모드별 RSI 임계값
# ============================================================
SECTOR_CRITERIA = {
    "semiconductor": {
        # 필수: PEG
        "peg_max": 0.8,              # 0.5 → 0.8 완화
        # PBR 제거 (사이클 상승장 영구 탈락 방지)
        # 선택 조건 (2/3 이상 충족)
        "rev_growth_min": 5,         # 반도체는 성장률 유연
        "consensus_gap_min": 0,
        "target_gap_min": 0,         # 목표주가 갭 > 0%
        # 거래량
        "volume_min_ratio": 1.0,     # 1.2 → 1.0 완화
        # RSI
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "ai_bigtech": {
        "peg_max": 1.2,
        # PBR 제거
        "rev_growth_min": 15,
        "consensus_gap_min": -10,    # 빅테크 유연
        "target_gap_min": 0,
        "volume_min_ratio": 1.0,
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "defense": {
        # 필수: PEG or PER (PBR 제거 — 방산은 자사주매입/배당으로 PBR 구조적 높음)
        "peg_max": 1.5,
        "per_max": 15,
        # 선택 조건
        "rev_growth_min": 5,
        "consensus_gap_min": 0,
        "target_gap_min": 0,
        "volume_min_ratio": 1.0,
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "aerospace": {
        # 흑자 기업 필수
        "peg_max": 1.5,
        # 적자 기업 대체
        "ps_max": 10,
        "band_max": 30,
        # PBR 제거 (적자 많아 참고 어려움)
        # 선택 조건
        "rev_growth_min": 5,
        "consensus_gap_min": 0,
        "target_gap_min": 0,
        "volume_min_ratio": 1.0,
        "rsi_normal": 40,
        "rsi_adjust": 35,
        "rsi_caution": 30,
        "rsi_panic": 25,
    },
    "industrial": {
        "peg_max": 1.2,
        "per_max": 12,
        "rev_growth_min": 5,
        "consensus_gap_min": 0,
        "rsi_normal": 40, "rsi_adjust": 35, "rsi_caution": 30, "rsi_panic": 25,
    },
    "growth": {
        "peg_max": 2.0,                    # 흑자 그로스주: PEG 관대
        "ps_max": 8,                       # 적자 그로스주: PS < 8
        "band_max": 40,                    # 52주 밴드 < 40%
        "rev_growth_min": 15,              # 매출 성장 핵심
        "consensus_gap_min": -10,          # 서프라이즈 -10% 까지 허용
        "rsi_normal": 40, "rsi_adjust": 35, "rsi_caution": 30, "rsi_panic": 25,
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
# 기울기 (Slope) 민감도 — EPS 컨센 + 목표주가 공통
# ============================================================
SLOPE_RULES = {
    # 매수 조건
    "required_mom_min": -1.0,    # slope_mom_pct ≥ -1% (필수, 매우 관대)
    # 가점 (강력 매수 승격 조건)
    "bonus_mom_min": 3.0,        # slope_mom_pct > +3%
    # 매도 트리거 (기업 위기)
    "crisis_eps_mom": -5.0,      # EPS slope_mom < -5% 2주 연속
    "crisis_target_mom": -3.0,   # 목표주가 slope_mom < -3% 2주 연속
    "crisis_consecutive_weeks": 2,
    # 정보성 알림 경고
    "raw_drop_pct": -3.0,        # raw 일별 변화 -3% 이상 급락 시 긴급 알림
}

# ============================================================
# EPS 추세 / Slope 계산 파라미터 (기존 유지)
# ============================================================
TREND_WINDOWS = {
    "curr_days": 5,    # V_curr: 최근 5거래일 평균
    "wow_offset": 7,   # V_wow: 7~11일 전
    "wow_days": 5,
    "mom_offset": 28,  # V_mom: 28~32일 전
    "mom_days": 5,
}

# ============================================================
# 매수 시그널 3단계
# ============================================================
BUY_LEVELS = {
    # 🟢 매수 후보: Step 1 통과 + RSI 매수 구간 (임계값 ~ 임계값+10)
    "candidate_rsi_upper_offset": 10,
    # 🟢🟢 강력 매수: Step 1 + 가점 2개 충족 (EPS slope > +3% AND 목표주가 slope > +3%)
    # 🚨 지금 매수: 매수 구간 + RSI 상향 돌파 (10분마다 체크, 돌파마다 발동)
    # 매수 후보 유효 기간
    "candidate_valid_days": 3,
}

# ============================================================
# 매도 트리거 — 3대 트리거 외엔 정보성만
# ============================================================
EXIT_TRIGGERS = {
    # 기업 위기: 모두 AND
    "crisis_market_drop_pct": -20.0,    # 지수 고점 대비 -20%
    "crisis_vix_panic": 40,             # VIX 40 이상
    # 물타기 기준
    "dca_drop_pct": -10.0,              # 평균 매수가 대비 -10% 하락 시 물타기 알림
}

# ============================================================
# 매크로 안전장치 (기존 유지)
# ============================================================
MACRO_GUARDS = {
    "qqq_ma_period": 20,
    "qqq_whipsaw_buffer": 0.01,
    "qqq_confirm_days": 2,
    "kospi_ticker": "^KS11",
    "kospi_ma_period": 20,
    # 시장 위기 트리거용 MA50
    "crisis_ma_period": 50,
}