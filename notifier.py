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
        return
    
    data = doc.data()
    all_stocks = data.get("kr", []) + data.get("us", [])
    
    new_state = {}
    alerts = []
    
    for stock in all_stocks:
        code = stock.get("code")
        name = stock.get("name")
        rsi = stock.get("rsi")
        step1 = stock.get("step1")
        
        if not rsi or not code:
            continue
        
        prev_rsi = prev_state.get(code, 50)
        new_state[code] = rsi
        
        # STEP1 통과 + RSI 35 하회 후 상향 돌파
        if step1 and prev_rsi < 35 and rsi >= 35:
            alerts.append({
                "name": name,
                "code": code,
                "rsi": rsi
            })
            print(f"🚨 시그널: {name} RSI {prev_rsi} → {rsi}")
    
    # 상태 저장
    db.collection("state").document("rsi").set(new_state)
    
    # 알림 발송
    if alerts:
        send_push_notifications(alerts)
    
    return alerts

def send_push_notifications(alerts):
    """FCM 토픽으로 푸시 알림 발송"""
    
    for alert in alerts:
        message = messaging.Message(
            notification=messaging.Notification(
                title=f"🚨 {alert['name']} 매수 시그널!",
                body=f"RSI {alert['rsi']} 돌파! STEP1+STEP2 충족"
            ),
            topic="stock-alerts"
        )
        
        try:
            response = messaging.send(message)
            print(f"✅ 알림 발송: {alert['name']} ({response})")
        except Exception as e:
            print(f"❌ 알림 실패: {e}")

if __name__ == "__main__":
    alerts = check_and_notify()
    if not alerts:
        print("📭 새 시그널 없음")