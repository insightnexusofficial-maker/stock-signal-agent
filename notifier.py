import firebase_admin
from firebase_admin import credentials, firestore, messaging

# Firebase 이미 초기화되어 있으면 스킵
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

def check_and_notify():
    """RSI 돌파 체크 및 알림 발송"""
    
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
        return
    
    data = doc.to_dict()
    
    # 모든 종목 합치기
    all_items = []
    all_items.extend([(s, "stock", 35) for s in data.get("kr_stock", [])])
    all_items.extend([(s, "etf", 30) for s in data.get("kr_etf", [])])
    all_items.extend([(s, "stock", 35) for s in data.get("us_stock", [])])
    
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

def send_push_notifications(alerts):
    """FCM 토큰들에게 푸시 알림 발송"""
    
    # Firestore에서 토큰 목록 가져오기
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
    alerts = check_and_notify()