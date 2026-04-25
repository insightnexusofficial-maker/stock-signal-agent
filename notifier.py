"""
사여?! - 알림 발송 (v4: 매수 3단계 + 위기 트리거 + EPS 정보성)
==========================================================
- 🟢 매수 후보 (candidate) / 🟢🟢 강력 매수 (strong)
- 🚨 지금 매수! (RSI 상향 돌파, 10분마다 재발동 가능)
- 🚨 기업 위기 / 🌪️ 시장 위기 (3대 트리거)
- 🟡 EPS 추세 정보성 (참고용)
- ⚡ VIX 반전 (특별 기회)
"""
import firebase_admin
from firebase_admin import credentials, firestore, messaging

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()


# ============================================================
# 유틸
# ============================================================
def _mode_emoji(mode):
    return {
        "normal": "🟢 일반",
        "adjust": "🟡 조정",
        "caution": "🟠 경계",
        "panic": "🔴 공포",
    }.get(mode, mode)


def send_push(title, body, tag=None, data=None):
    """등록된 모든 FCM 토큰으로 푸시 발송."""
    tokens_ref = db.collection("fcm_tokens").stream()
    tokens = [doc.id for doc in tokens_ref]
    
    if not tokens:
        print(f"   ⚠️  토큰 없음: {title}")
        return
    
    sent = 0
    failed = 0
    for token in tokens:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                token=token,
                data=data or {},
                android=messaging.AndroidConfig(
                    notification=messaging.AndroidNotification(tag=tag) if tag else None
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            thread_id=tag or "default",
                            sound="default",
                        )
                    )
                ),
            )
            messaging.send(message)
            sent += 1
        except Exception as e:
            failed += 1
            # 만료 토큰 정리
            if "Requested entity was not found" in str(e) or "registration-token-not-registered" in str(e):
                try:
                    db.collection("fcm_tokens").document(token).delete()
                except:
                    pass
    
    print(f"   📨 {title}: 발송 {sent} / 실패 {failed}")


# ============================================================
# 메인 알림 로직
# ============================================================
def check_and_notify(vix_data=None, qqq_data=None, kospi_data=None):
    """
    매크로 + 종목 시그널 검토 후 알림 발송.
    
    발송 대상:
    1. 🟢 매수 후보 / 🟢🟢 강력 매수 (신규 진입)
    2. 🚨 지금 매수! (RSI 임계값 상향 돌파, 매수 후보 종목)
    3. 🚨 기업 위기 / 🌪️ 시장 위기
    4. 🟡 EPS 추세 정보성 (참고용)
    5. ⚡ VIX 반전 (공포 → 평온 전환)
    """
    print(f"\n📊 시장 모드: 미국 {_mode_emoji((qqq_data or {}).get('above_ma20', True) and 'normal' or 'adjust')} | "
          f"한국 {_mode_emoji((kospi_data or {}).get('above_ma20', True) and 'normal' or 'adjust')} "
          f"(VIX: {(vix_data or {}).get('current', '?')})")
    
    # === Firestore 데이터 로드 ===
    try:
        doc = db.collection("stocks").document("data").get()
        if not doc.exists:
            print("   📭 stocks/data 문서 없음")
            return
        data = doc.to_dict()
    except Exception as e:
        print(f"   ⚠️  Firestore 로드 실패: {e}")
        return
    
    all_stocks = (data.get("kr_stock") or []) + (data.get("us_stock") or []) + (data.get("kr_etf") or [])
    
    # === 이전 RSI 상태 로드 (돌파 감지용) ===
    try:
        rsi_state_doc = db.collection("state").document("rsi").get()
        prev_rsi_map = rsi_state_doc.to_dict() if rsi_state_doc.exists else {}
    except:
        prev_rsi_map = {}
    
    new_rsi_map = {}
    sent_count = 0
    
    # === 종목별 시그널 처리 ===
    for stock in all_stocks:
        code = stock.get("code")
        name = stock.get("name", "?")
        if not code:
            continue
        
        rsi = stock.get("rsi")
        rsi_threshold = stock.get("rsi_threshold")
        buy_level = stock.get("buy_level", "none")
        in_zone = stock.get("in_buy_zone", False)
        step1 = stock.get("step1", False)
        sector = stock.get("sector", "")
        price = stock.get("price")
        
        # 새 RSI 상태 저장용
        if rsi is not None:
            new_rsi_map[code] = rsi
        
        prev_rsi = prev_rsi_map.get(code)
        
        # ============================================================
        # 1. 🚨 지금 매수! — RSI 임계값 상향 돌파 (10분마다 재발동 가능)
        # ============================================================
        if step1 and in_zone and rsi is not None and rsi_threshold is not None and prev_rsi is not None:
            crossed_up = prev_rsi < rsi_threshold and rsi >= rsi_threshold
            if crossed_up:
                emoji = "🚨💪" if buy_level == "strong" else "🚨"
                level_label = "강력 매수" if buy_level == "strong" else "매수 후보"
                
                title = f"{emoji} 지금 매수! {name}"
                body = (f"RSI {prev_rsi:.1f} → {rsi:.1f} (기준 {rsi_threshold}) | "
                        f"{level_label} | {price}")
                send_push(title, body, tag=f"buy-now-{code}", data={
                    "type": "buy_now",
                    "code": code,
                    "level": buy_level,
                })
                sent_count += 1
                continue  # "지금 매수" 떴으면 일반 매수 알림 중복 발송 안 함
        
        # ============================================================
        # 2. 🟢 매수 후보 / 🟢🟢 강력 매수 — 첫 진입 시 알림
        # ============================================================
        # 이전엔 in_zone False였는데 이번에 True로 진입한 경우
        prev_state_doc_id = f"buy_zone_{code}"
        try:
            prev_zone_doc = db.collection("state").document(prev_state_doc_id).get()
            prev_in_zone = prev_zone_doc.to_dict().get("in_zone", False) if prev_zone_doc.exists else False
        except:
            prev_in_zone = False
        
        if step1 and in_zone and not prev_in_zone:
            if buy_level == "strong":
                title = f"🟢🟢 강력 매수: {name}"
                body = f"RSI {rsi} (구간 {rsi_threshold}~{rsi_threshold + 10}) | EPS·목표 동반 개선 | {price}"
            elif buy_level == "candidate":
                title = f"🟢 매수 후보: {name}"
                body = f"RSI {rsi} (구간 {rsi_threshold}~{rsi_threshold + 10}) | hits {stock.get('selection_hits', '?')}/3 | {price}"
            else:
                title = None
            
            if title:
                send_push(title, body, tag=f"buy-{code}", data={
                    "type": "buy_zone_entry",
                    "code": code,
                    "level": buy_level,
                })
                sent_count += 1
        
        # 매수 구간 상태 저장
        try:
            db.collection("state").document(prev_state_doc_id).set({"in_zone": in_zone})
        except:
            pass
        
        # ============================================================
        # 3. 🚨 기업 위기 / 🌪️ 시장 위기
        # ============================================================
        triggers = stock.get("crisis_triggers") or []
        details = stock.get("crisis_details") or []
        
        for i, trigger in enumerate(triggers):
            detail = details[i] if i < len(details) else ""
            
            # 중복 발송 방지
            crisis_state_id = f"crisis_{trigger}_{code}"
            try:
                prev_crisis = db.collection("state").document(crisis_state_id).get()
                already_sent = prev_crisis.exists and prev_crisis.to_dict().get("sent", False)
            except:
                already_sent = False
            
            if already_sent:
                continue
            
            if trigger == "company_crisis":
                title = f"🚨 기업 위기: {name}"
                body = detail if detail else "EPS+목표주가 동반 하락 + 매출/서프 쇼크"
            elif trigger == "market_panic":
                title = f"🌪️ 시장 위기 진입"
                body = detail if detail else "VIX 40+ 및 지수 MA 하향. 현금화 검토."
            else:
                continue
            
            send_push(title, body, tag=f"crisis-{trigger}-{code}", data={
                "type": "crisis",
                "trigger": trigger,
                "code": code,
            })
            sent_count += 1
            
            try:
                db.collection("state").document(crisis_state_id).set({"sent": True})
            except:
                pass
        
        # ============================================================
        # 4. 🟡 EPS 추세 정보성 (참고용, 매도 권고 아님)
        # ============================================================
        info_level = stock.get("info_level")
        info_reasons = stock.get("info_reasons") or []
        
        if info_level:
            # 같은 레벨 중복 발송 방지 (하루 1번)
            info_state_id = f"info_{code}"
            try:
                prev_info_doc = db.collection("state").document(info_state_id).get()
                prev_info_level = prev_info_doc.to_dict().get("level") if prev_info_doc.exists else None
            except:
                prev_info_level = None
            
            if prev_info_level != info_level:
                if info_level == "info_watch":
                    emoji_label = "🟠 관찰"
                elif info_level == "info_warn":
                    emoji_label = "🟡 주의"
                else:
                    emoji_label = "🔵 정보"
                
                title = f"{emoji_label}: {name}"
                body = " | ".join(info_reasons) if info_reasons else "EPS 추세 변화"
                send_push(title, body, tag=f"info-{code}", data={
                    "type": "info",
                    "level": info_level,
                    "code": code,
                })
                sent_count += 1
                
                try:
                    db.collection("state").document(info_state_id).set({"level": info_level})
                except:
                    pass
    
    # === RSI 상태 저장 (다음 tick에서 돌파 감지용) ===
    if new_rsi_map:
        try:
            db.collection("state").document("rsi").set(new_rsi_map)
        except Exception as e:
            print(f"   ⚠️  RSI 상태 저장 실패: {e}")
    
    # ============================================================
    # 5. ⚡ VIX 반전 (공포 → 평온 전환)
    # ============================================================
    if vix_data and vix_data.get("reversal"):
        try:
            vix_state = db.collection("state").document("vix_reversal").get()
            already = vix_state.exists and vix_state.to_dict().get("sent_today", False)
        except:
            already = False
        
        if not already:
            current = vix_data.get("current", "?")
            send_push(
                "⚡ VIX 하락 반전",
                f"VIX {current} 꺾임. 공포 완화 시작 — 분할 매수 검토.",
                tag="vix-reversal",
                data={"type": "vix_reversal"},
            )
            sent_count += 1
            try:
                db.collection("state").document("vix_reversal").set({"sent_today": True})
            except:
                pass
    
    # === 결과 ===
    if sent_count == 0:
        print("   📭 새 시그널 없음")
    else:
        print(f"   ✅ 총 {sent_count}건 발송")