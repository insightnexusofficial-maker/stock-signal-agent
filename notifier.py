import firebase_admin
from firebase_admin import credentials, firestore, messaging
from config import EXIT_RULES

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ============================================================
# 헬퍼
# ============================================================
def _mode_emoji(mode):
    return {
        "normal": "🟢 일반",
        "adjust": "🟡 조정",
        "caution": "🟠 경계",
        "panic": "🔴 공포",
    }.get(mode, mode)

def send_push(title, body, tag=None, data=None):
    """approved=true인 FCM 토큰에 푸시 발송."""
    try:
        tokens_ref = db.collection("fcm_tokens").where("approved", "==", True).stream()
        tokens = [doc.id for doc in tokens_ref]
        
        if not tokens:
            print(f"   [{title}] 토큰 없음 - 스킵")
            return 0
        
        sent = 0
        for token in tokens:
            try:
                msg = messaging.Message(
                    notification=messaging.Notification(title=title, body=body),
                    token=token,
                    data={k: str(v) for k, v in (data or {}).items()},
                )
                messaging.send(msg)
                sent += 1
            except Exception as e:
                print(f"   FCM 개별 실패 ({token[:10]}...): {e}")
        
        print(f"   📬 [{title}] {sent}/{len(tokens)}건 발송")
        return sent
    except Exception as e:
        print(f"   FCM 에러: {e}")
        return 0

# ============================================================
# 메인
# ============================================================
def check_and_notify(vix_data=None, qqq_data=None, kospi_data=None):
    """
    시그널 판정 + 알림 발송.
      - 매수: step1 통과 + RSI 상향 돌파 (어제 < 임계 ≤ 오늘)
      - 매도: exit_level 기반 단계화 (warn / sell_half / sell_full)
      - VIX 하락 반전: 별도 특별 알림
    """
    vix_current = vix_data.get("current", 0) if vix_data else 0
    vix_reversal = vix_data.get("reversal", False) if vix_data else False
    vix_mode = vix_data.get("mode", "normal") if vix_data else "normal"
    
    # 모드 판정 (region별)
    def _mode(idx):
        if vix_mode == "level2": return "panic"
        if vix_mode == "level1": return "caution"
        if idx and not idx.get("above_ma20", True): return "adjust"
        return "normal"
    
    mode_us = _mode(qqq_data)
    mode_kr = _mode(kospi_data)
    
    print(f"📊 시장 모드: 미국 {_mode_emoji(mode_us)} | 한국 {_mode_emoji(mode_kr)} (VIX: {vix_current})")
    
    # VIX 하락 반전 → 최고 매수 기회 알림
    if mode_us in ["caution", "panic"] and vix_reversal:
        send_push(
            title="⚡ VIX 하락 반전",
            body=f"VIX {vix_current} 꺾임 — 공포 완화 시작. 분할 매수 검토.",
            tag="vix_reversal",
            data={"type": "vix_reversal"},
        )
    
    # 데이터 로드
    doc = db.collection("stocks").document("data").get()
    if not doc.exists:
        print("📭 stocks/data 없음")
        return []
    data = doc.to_dict()
    
    # 이전 상태 로드
    prev_rsi_doc = db.collection("state").document("rsi").get()
    prev_rsi = prev_rsi_doc.to_dict() if prev_rsi_doc.exists else {}
    
    exit_cnt_doc = db.collection("state").document("exit_counter").get()
    exit_counter = exit_cnt_doc.to_dict() if exit_cnt_doc.exists else {}
    
    new_rsi_state = {}
    new_exit_counter = {}
    buy_alerts = []
    exit_alerts = []
    
    # 섹션별 처리
    sections = [
        ("kr_stock", "kr"),
        ("kr_etf", "kr"),
        ("us_stock", "us"),
    ]
    
    for section_key, region in sections:
        for item in data.get(section_key, []):
            code = item.get("code")
            name = item.get("name", "?")
            rsi = item.get("rsi")
            rsi_threshold = item.get("rsi_threshold")
            
            if code is None or rsi is None or rsi_threshold is None:
                continue
            
            # 다음 실행 비교용으로 현재 RSI 저장
            new_rsi_state[code] = rsi
            
            # === 매수: RSI 상향 돌파 ===
            prev = prev_rsi.get(code)
            crossed_up = (
                prev is not None
                and prev < rsi_threshold
                and rsi >= rsi_threshold
            )
            
            if item.get("step1") and crossed_up:
                buy_alerts.append({
                    "name": name,
                    "code": code,
                    "rsi": rsi,
                    "prev_rsi": prev,
                    "threshold": rsi_threshold,
                    "sector": item.get("sector", ""),
                    "price": item.get("price"),
                })
            
            # === 매도: exit_level 단계화 ===
            exit_level = item.get("exit_level")
            exit_reasons = item.get("exit_reasons", [])
            
            if exit_level == "sell_full":
                exit_alerts.append({
                    "name": name, "code": code,
                    "level": "sell_full", "reasons": exit_reasons,
                })
                new_exit_counter[code] = 0  # 리셋
            
            elif exit_level == "warn":
                prev_cnt = exit_counter.get(code, 0)
                new_cnt = prev_cnt + 1
                new_exit_counter[code] = new_cnt
                
                sell_threshold = EXIT_RULES.get("wow_sell_count", 2)
                warn_threshold = EXIT_RULES.get("wow_warn_count", 1)
                
                if new_cnt >= sell_threshold:
                    exit_alerts.append({
                        "name": name, "code": code,
                        "level": "sell_half", "reasons": exit_reasons,
                        "streak": new_cnt,
                    })
                elif new_cnt >= warn_threshold:
                    exit_alerts.append({
                        "name": name, "code": code,
                        "level": "warn", "reasons": exit_reasons,
                        "streak": new_cnt,
                    })
            else:
                # 정상 → 카운터 리셋
                new_exit_counter[code] = 0
    
    # state 저장
    db.collection("state").document("rsi").set(new_rsi_state)
    db.collection("state").document("exit_counter").set(new_exit_counter)
    
    # === 알림 발송 ===
    # 매수
    for a in buy_alerts:
        price_str = f"{a['price']:,.0f}" if a.get("price") else "?"
        send_push(
            title=f"🔔 매수 돌파: {a['name']}",
            body=f"RSI {a['prev_rsi']:.1f}→{a['rsi']:.1f} (기준 {a['threshold']}) · {price_str} · {a['sector']}",
            tag=f"buy_{a['code']}",
            data={"type": "buy", "code": a["code"]},
        )
    
    # 매도
    level_cfg = {
        "sell_full": ("🚨 전량 매도", "exit_full"),
        "sell_half": ("⚠️ 절반 매도", "exit_half"),
        "warn":      ("⚡ 매도 경고", "exit_warn"),
    }
    for a in exit_alerts:
        icon, t = level_cfg.get(a["level"], ("⚡", "exit"))
        reason_text = " / ".join(a.get("reasons", []))
        streak = a.get("streak")
        streak_str = f" ({streak}회 연속)" if streak and streak > 1 else ""
        send_push(
            title=f"{icon}: {a['name']}{streak_str}",
            body=reason_text or "매도 조건 충족",
            tag=f"{t}_{a['code']}",
            data={"type": a["level"], "code": a["code"]},
        )
    
    total = len(buy_alerts) + len(exit_alerts)
    if total == 0:
        print("📭 새 시그널 없음")
    else:
        print(f"📬 매수 {len(buy_alerts)}건, 매도 {len(exit_alerts)}건")
    
    return buy_alerts + exit_alerts