import firebase_admin
from firebase_admin import credentials, firestore, messaging
from runtime_secrets import load_runtime_env, get_firebase_key_path

load_runtime_env()

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate(get_firebase_key_path())
    firebase_admin.initialize_app(cred)

db = firestore.client()

def check_and_notify(vix_data=None, qqq_data=None):
    """VIX/QQQ 모드에 따른 알림 분기"""
    vix_mode = vix_data.get("mode", "normal") if vix_data else "normal"
    vix_reversal = vix_data.get("reversal", False) if vix_data else False
    vix_current = vix_data.get("current", 0) if vix_data else 0

    qqq_above = qqq_data.get("above_ma20", True) if qqq_data else True
    market_mode = "normal" if qqq_above else "caution"

    print(f"📊 VIX 모드: {vix_mode.upper()} (VIX: {vix_current})")
    print(f"📊 QQQ 모드: {'🟢 상승' if qqq_above else '🟡 경계'}")

    # Level 1/2에서 VIX 하락 반전 시 특별 알림
    if vix_mode in ["level1", "level2"] and vix_reversal:
        send_panic_alert(vix_mode, vix_current)
        return []

    # Level 1/2에서는 일반 알림 Mute
    if vix_mode in ["level1", "level2"]:
        print("🔇 일반 알림 Mute (VIX ≥ 25)")
        return []

    # Normal/Caution 모드: 일반 시그널 체크
    return check_normal_signals(market_mode)

def check_normal_signals(market_mode):
    """평상시 RSI 돌파 체크 및 알림 발송"""
    
    # 이전 상태 로드
    try:
        prev_doc = db.collection("state").document("rsi").get()
        prev_state = prev_doc.to_dict() if prev_doc.exists else {}
    except:
        prev_state = {}
    
    # 현재 데이터 로드
    doc = db.collection("stocks").document("data").get()
    if not doc.exists:
        print("📭 데이터 없음")
        return []
    
    data = doc.to_dict()
    
    # RSI 기준: 일반 40, 경계 30
    stock_rsi = 40 if market_mode == "normal" else 30
    etf_rsi = 30 if market_mode == "normal" else 25

    # 모든 종목 합치기
    all_items = []
    all_items.extend([(s, "stock", stock_rsi) for s in data.get("kr_stock", [])])
    all_items.extend([(s, "etf", etf_rsi) for s in data.get("kr_etf", [])])
    all_items.extend([(s, "stock", stock_rsi) for s in data.get("us_stock", [])])
    
    new_state = {}
    alerts = []
    
    for item, item_type, rsi_threshold in all_items:
        code = item.get("code")
        name = item.get("name")
        rsi = item.get("rsi")
        step1 = item.get("step1")
        
        if not rsi or not code:
            continue
        
        prev_rsi = prev_state.get(code, 50)
        new_state[code] = rsi
        
        # STEP1 통과 + RSI 임계값 하회 후 상향 돌파
        if step1 and prev_rsi < rsi_threshold and rsi >= rsi_threshold:
            alerts.append({
                "name": name,
                "code": code,
                "rsi": rsi,
                "type": item_type
            })
            print(f"🚨 시그널: {name} RSI {prev_rsi} → {rsi}")
    
    # 상태 저장
    db.collection("state").document("rsi").set(new_state)
    
    # 알림 발송
    if alerts:
        send_push_notifications(alerts)
    else:
        print("📭 새 시그널 없음")
    
    return alerts

def send_panic_alert(mode, vix_current):
    """패닉장 VIX 하락 반전 시 특별 알림"""
    
    tokens_ref = db.collection("fcm_tokens").where("approved", "==", True).stream()
    tokens = [doc.id for doc in tokens_ref]
    
    if not tokens:
        print("📭 등록된 토큰 없음")
        return
    
    if mode == "level2":
        title = "🔥 강력 매수 기회!"
        body = f"VIX {vix_current} 하락 반전 감지! 공포장 저점 매수 타이밍"
    else:
        title = "⚡ 패닉 저점 기회!"
        body = f"VIX {vix_current} 하락 반전 감지! 분할 매수 고려"
    
    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=body
        ),
        tokens=tokens
    )
    
    try:
        response = messaging.send_each_for_multicast(message)
        print(f"✅ 패닉 알림 발송 (성공: {response.success_count})")
    except Exception as e:
        print(f"❌ 패닉 알림 실패: {e}")

def send_push_notifications(alerts):
    """FCM 토큰들에게 푸시 알림 발송"""
    
    tokens_ref = db.collection("fcm_tokens").where("approved", "==", True).stream()
    tokens = [doc.id for doc in tokens_ref]
    
    if not tokens:
        print("📭 등록된 토큰 없음")
        return
    
    for alert in alerts:
        type_label = "ETF" if alert["type"] == "etf" else "주식"
        
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=f"🚨 {alert['name']} 매수 시그널!",
                body=f"[{type_label}] RSI {alert['rsi']} 돌파! 시그널 조건 충족"
            ),
            tokens=tokens
        )
        
        try:
            response = messaging.send_each_for_multicast(message)
            print(f"✅ 알림 발송: {alert['name']} (성공: {response.success_count})")
        except Exception as e:
            print(f"❌ 알림 실패: {e}")

if __name__ == "__main__":
    check_and_notify()